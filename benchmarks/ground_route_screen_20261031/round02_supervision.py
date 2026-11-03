"""Coarse user-reviewed supervision and a bounded task-definition training probe.

This small CNN is not PP-LiteSeg. Its timing must never inherit round01 numbers.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import random
import shutil
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw
import torch
from torch import nn
from torch.nn import functional as F

TASK = Path(__file__).resolve().parent
R1 = TASK/'runs/round01'
OUT = TASK/'runs/round02_20261101'
TRAIN_IDS = ['A01_02','A02_02','A03_02','A04_02','A07_02','A08_02']
PROBE_IDS = ['A05_02','A06_02']


def save(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def coarse_mask(raw):
    """Ignore uncertain boundaries, preserve only cores of coarse regions."""
    raw = np.asarray(raw, dtype=np.uint8)
    result = np.full(raw.shape, 255, np.uint8)
    for cls, size in [(0, 11), (1, 9), (2, 3)]:
        core = cv2.erode((raw == cls).astype(np.uint8), np.ones((size,size),np.uint8),
                         borderType=cv2.BORDER_CONSTANT, borderValue=0)
        result[core > 0] = cls
    return result


def binary_target(target):
    result = (target == 1).astype(np.uint8)
    result[target == 255] = 255
    return result


class Block(nn.Sequential):
    def __init__(self, cin, cout, stride=1):
        super().__init__(nn.Conv2d(cin,cout,3,stride,1,bias=False), nn.BatchNorm2d(cout), nn.ReLU())


class TinyTaskProbe(nn.Module):
    def __init__(self, classes):
        super().__init__()
        self.e1=Block(3,16,2)
        self.e2=Block(16,24,2)
        self.e3=Block(24,48,2)
        self.e4=Block(48,96,2)
        self.d3=Block(144,48)
        self.d2=Block(72,24)
        self.d1=Block(40,16)
        self.classifier=nn.Conv2d(16,classes,1)

    def forward(self, x):
        a=self.e1(x);b=self.e2(a);c=self.e3(b);d=self.e4(c)
        d=self.d3(torch.cat([F.interpolate(d,size=c.shape[2:],mode='nearest'),c],1))
        d=self.d2(torch.cat([F.interpolate(d,size=b.shape[2:],mode='nearest'),b],1))
        d=self.d1(torch.cat([F.interpolate(d,size=a.shape[2:],mode='nearest'),a],1))
        return F.interpolate(self.classifier(d),size=x.shape[2:],mode='nearest')


def prepare(evidence):
    OUT.mkdir(exist_ok=False)
    folder=OUT/'coarse_labels';folder.mkdir()
    labels=json.loads((R1/'annotation_drafts/labels.json').read_text(encoding='utf-8'))
    entries=[]
    for row in labels['entries']:
        sample_id=row['sample_id']
        raw=np.asarray(Image.open(R1/'annotation_drafts'/row['mask']))
        masked=coarse_mask(raw)
        Image.fromarray(masked).save(folder/(sample_id+'.png'))
        entries.append(dict(sample_id=sample_id,split='train' if sample_id in TRAIN_IDS else 'development_object_probe',
                            source_sha256=row['source_sha256'],label_file=sample_id+'.png',
                            original_draft_mask_sha256=hashlib.sha256((R1/'annotation_drafts'/row['mask']).read_bytes()).hexdigest(),
                            counts={str(c):int(np.sum(masked==c)) for c in [0,1,2,255]}))
    shutil.copyfile(evidence,OUT/'user_chair_review.png')
    approval=dict(date='2026-11-01',user_quote='基本都对，椅子就这样，应该也是正常的吧',
                  status='overall_coarse_review_accepted',
                  scope='Overall acceptance of eight visual drafts, specifically chair gaps; not exact per-pixel annotation',
                  interpretation='Use as weak/coarse development supervision; object gaps showing visible ground remain floor',
                  human_per_pixel_verified=False,complete_obstacle_annotation=False,
                  independent_ground_truth=False,annotator_saw_model_previews=True,
                  allowed_for_bounded_development_training=True,allowed_for_independent_accuracy_claim=False,
                  evidence_sha256=hashlib.sha256(Path(evidence).read_bytes()).hexdigest(),entries=entries)
    save(OUT/'coarse_review_acceptance.json',approval)
    protocol=dict(model='TinyTaskProbe scratch CNN; not PP-LiteSeg and not deployment-selected',
                  input_wh=[320,240],train_ids=TRAIN_IDS,development_probe_ids=PROBE_IDS,
                  seed=20261101,steps=250,optimizer='AdamW lr=0.001 weight_decay=0.0001',
                  same_body_initialization=True,same_augmentations=True,
                  pixel_weights='primary-obstacle annotated pixels x5 in BOTH tasks; other valid pixels x1',
                  augmentation='horizontal flip, brightness +/-0.1, contrast +/-0.1; all label transforms paired',
                  labels='weak coarse user-reviewed; boundary ignored; class0 coarse context, not exhaustive obstacle truth',
                  checkpoint_selection='final fixed step, no choosing on probe scores',
                  evaluation='coarse development diagnostics only; two probes share corridor; no independent generalization claim',
                  heldout_scene_B_used=False,board_timing_inherited=False)
    save(OUT/'protocol.json',protocol)
    print('Prepared coarse labels with scoped user review; independent-accuracy permission remains false.',flush=True)


def dataset():
    manifest=json.loads((R1/'data/manifest.json').read_text(encoding='utf-8'))
    sources={x['sample_id']:x for x in manifest['samples'] if x['split']=='development_scene_A'}
    arrays={}
    for sample_id in TRAIN_IDS+PROBE_IDS:
        source=sources[sample_id];data=(R1/'data'/source['file']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=source['sha256']:raise ValueError('Input changed')
        rgb=np.asarray(Image.open(R1/'data'/source['file']).convert('RGB'))
        rgb=cv2.resize(rgb,(320,240),interpolation=cv2.INTER_LINEAR)
        mask=np.asarray(Image.open(OUT/'coarse_labels'/(sample_id+'.png')))
        mask=cv2.resize(mask,(320,240),interpolation=cv2.INTER_NEAREST)
        arrays[sample_id]=(rgb,mask)
    return arrays


def body_hash(model):
    digest=hashlib.sha256()
    for name,tensor in model.state_dict().items():
        if not name.startswith('classifier.'):
            digest.update(name.encode());digest.update(tensor.cpu().numpy().tobytes())
    return digest.hexdigest()


def train(kind, arrays, shared_state, device):
    binary=kind=='floor_only'
    torch.manual_seed(20261101)
    model=TinyTaskProbe(2 if binary else 3).to(device)
    state=model.state_dict();state.update({k:v.to(device) for k,v in shared_state.items()})
    model.load_state_dict(state)
    initial_hash=body_hash(model)
    images=torch.from_numpy(np.stack([arrays[k][0].transpose(2,0,1) for k in TRAIN_IDS])).float().to(device)/255
    masks=np.stack([arrays[k][1] for k in TRAIN_IDS])
    target=torch.from_numpy(np.stack([binary_target(x) for x in masks]) if binary else masks).long().to(device)
    weight=torch.from_numpy(np.where(masks==2,5.,1.).astype(np.float32)).to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.0001)
    generator=torch.Generator(device=device).manual_seed(71001)
    logs=[];started=time.perf_counter();model.train()
    folder=OUT/kind;folder.mkdir()
    with (folder/'train.jsonl').open('x',encoding='utf-8') as log:
        for step in range(250):
            x=images.clone();y=target.clone();w=weight.clone()
            flip=torch.rand((len(TRAIN_IDS),),generator=generator,device=device)<.5
            x[flip]=x[flip].flip(-1);y[flip]=y[flip].flip(-1);w[flip]=w[flip].flip(-1)
            contrast=.9+.2*torch.rand((len(TRAIN_IDS),1,1,1),generator=generator,device=device)
            brightness=-.1+.2*torch.rand((len(TRAIN_IDS),1,1,1),generator=generator,device=device)
            x=((x-.5)*contrast+.5+brightness).clamp(0,1)*2-1
            optimizer.zero_grad(set_to_none=True)
            logits=model(x)
            losses=F.cross_entropy(logits,y,ignore_index=255,reduction='none')
            valid=(y!=255).float();loss=(losses*w*valid).sum()/(w*valid).sum()
            loss.backward();optimizer.step()
            if step==0 or (step+1)%50==0:
                row=dict(step=step+1,loss=float(loss.detach().cpu()),elapsed_s=time.perf_counter()-started)
                logs.append(row);log.write(json.dumps(row)+'\n');log.flush();print(kind,row,flush=True)
    model.eval();torch.cuda.synchronize() if device=='cuda' else None
    state={k:v.detach().cpu() for k,v in model.state_dict().items()}
    torch.save(dict(model='TinyTaskProbe',classes=2 if binary else 3,state_dict=state,
                    source='coarse development training only',protocol='protocol.json'),folder/'checkpoint.pt')
    with torch.no_grad():
        metrics=[]
        for sample_id,(rgb,mask) in arrays.items():
            x=torch.from_numpy(rgb.transpose(2,0,1).copy()).float().unsqueeze(0).to(device)/127.5-1
            pred=model(x).argmax(1)[0].cpu().numpy().astype(np.uint8)
            Image.fromarray(pred).save(folder/(sample_id+'_prediction.png'))
            expected=binary_target(mask) if binary else mask
            valid=expected!=255;true_floor=expected==1;pred_floor=pred==1
            floor_inter=np.sum(true_floor & pred_floor & valid);floor_union=np.sum((true_floor | pred_floor)&valid)
            row=dict(sample_id=sample_id,split='train' if sample_id in TRAIN_IDS else 'development_object_probe',
                     floor_iou=float(floor_inter/max(1,floor_union)),scored_pixels=int(valid.sum()))
            if not binary:
                a=expected==2;b=pred==2
                row['primary_obstacle_iou']=float(np.sum(a&b&valid)/max(1,np.sum((a|b)&valid)))
                row['primary_obstacle_pixel_recall']=float(np.sum(a&b&valid)/max(1,np.sum(a&valid)))
            metrics.append(row)
    save(folder/'diagnostics.json',dict(scope='Coarse development diagnostics, not independent accuracy',
                                      initialization_body_sha256=initial_hash,elapsed_s=time.perf_counter()-started,
                                      parameters=sum(v.numel() for v in model.parameters()),rows=metrics,training_log=logs))
    cpu_model=copy.deepcopy(model).cpu()
    example=torch.from_numpy(arrays[TRAIN_IDS[0]][0].transpose(2,0,1).copy()).float().unsqueeze(0)/127.5-1
    torch.onnx.export(cpu_model,example,str(folder/'model.onnx'),opset_version=11,
                      input_names=['image'],output_names=['logits'],dynamo=False)
    import onnxruntime as ort
    options=ort.SessionOptions();options.intra_op_num_threads=1
    session=ort.InferenceSession(str(folder/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    comparisons=[]
    with torch.no_grad():
        for sample_id in TRAIN_IDS+PROBE_IDS:
            x=torch.from_numpy(arrays[sample_id][0].transpose(2,0,1).copy()).float().unsqueeze(0)/127.5-1
            reference=cpu_model(x).numpy();exported=session.run(None,{'image':x.numpy()})[0]
            comparisons.append(dict(sample_id=sample_id,max_logit_abs=float(np.max(np.abs(reference-exported))),
                                    category_different_pixels=int(np.sum(reference.argmax(1)!=exported.argmax(1)))))
    save(folder/'onnx_consistency.json',dict(scope='Same trained weights PyTorch FP32 vs ONNX FP32; not quantized BIN',rows=comparisons))
    return initial_hash


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',type=Path)
    args=parser.parse_args()
    if args.prepare:prepare(args.prepare);return
    if not OUT.exists():raise RuntimeError('Prepare reviewed coarse data first')
    torch.set_num_threads(4)
    torch.manual_seed(20261101);np.random.seed(20261101);random.seed(20261101)
    torch.backends.cudnn.deterministic=True;torch.backends.cudnn.benchmark=False
    if not torch.cuda.is_available():raise RuntimeError('This bounded training probe expects the available GPU')
    save(OUT/'environment.json',dict(torch=torch.__version__,gpu=torch.cuda.get_device_name(0),
                                  scope='PC training only; not BPU performance'))
    template=TinyTaskProbe(3)
    shared={k:v.clone() for k,v in template.state_dict().items() if not k.startswith('classifier.')}
    arrays=dataset()
    a=train('floor_only',arrays,shared,'cuda')
    b=train('floor_obstacle_context',arrays,shared,'cuda')
    if a!=b:raise RuntimeError('A/B did not start from identical body weights')
    save(OUT/'completion.json',dict(status='completed',body_initialization_equal=True,
                                  trained_models=2,training_images=6,development_probe_images=2,
                                  heldout_scene_B_used=False,independent_accuracy_claim=False,
                                  board_deployment_verified=False,round01_timing_applies=False))


if __name__=='__main__':main()
