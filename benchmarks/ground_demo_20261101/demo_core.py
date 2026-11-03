"""Causal demo logic from predicted floor only, with no reference masks."""
import cv2
import numpy as np

MAX_AGE_S=.5


def preprocess(bgr):
    rgb=cv2.cvtColor(cv2.resize(bgr,(320,240)),cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray((rgb.astype(np.float32)/127.5-1).transpose(2,0,1)[None])


def featuremap_bytes(x):
    """Current hobot_dnn binding takes byte buffers even for F32 tensors.

    Preserve the IEEE754 payload; astype(uint8) would destroy the input.
    Verified against native hrt output and the same quantized emulator.
    """
    if x.dtype!=np.float32 or x.shape!=(1,3,240,320) or not x.flags.c_contiguous:
        raise ValueError('F32 NCHW contiguous input required')
    return x.view(np.uint8).reshape(-1)


def categories(logits):
    raw=np.asarray(logits)
    if not np.isfinite(raw).all():raise ValueError('Nonfinite model output')
    if raw.shape==(1,2,240,320):return raw.argmax(1)[0].astype(np.uint8)
    if raw.shape==(1,240,320,2):return raw.argmax(-1)[0].astype(np.uint8)
    raise ValueError('Unexpected floor logits shape: '+str(raw.shape))


def candidates(category):
    if category.shape!=(240,320) or not np.isin(category,[0,1]).all():raise ValueError('Wrong category contract')
    floor=(category==1).astype(np.uint8)
    n,cc,stats,_=cv2.connectedComponentsWithStats(floor,8)
    if n<2:return dict(status='UNKNOWN',direction=None,boxes=[],reason='insufficient_floor'),np.zeros_like(floor)
    best=1+np.argmax(stats[1:,cv2.CC_STAT_AREA]);main=cc==best
    if main[125:].sum()<320*115*.08:
        return dict(status='UNKNOWN',direction=None,boxes=[],reason='insufficient_floor'),np.zeros_like(floor)
    envelope=np.zeros_like(floor)
    for y in range(125,234):
        xs=np.flatnonzero(main[y])
        if len(xs)>=16 and xs[-1]-xs[0]>=32:
            envelope[y,max(16,xs[0]):min(304,xs[-1]+1)]=1
    holes=envelope*(1-floor)
    holes=cv2.morphologyEx(holes,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    count,_,stats,centers=cv2.connectedComponentsWithStats(holes,8)
    boxes=[]
    for index in range(1,count):
        x,y,w,h,area=map(int,stats[index])
        if area<80 or w<8 or h<10:continue
        cx=float(centers[index,0]);direction='LEFT' if cx<128 else 'RIGHT' if cx>192 else 'CENTER'
        boxes.append(dict(x=x,y=y,w=w,h=h,area=area,direction=direction))
    boxes.sort(key=lambda b:b['area'],reverse=True)
    return dict(status='CANDIDATE' if boxes else 'NO_CANDIDATE',direction=boxes[0]['direction'] if boxes else None,
                boxes=boxes,reason='predicted_floor_gap' if boxes else 'no_gap_above_demo_gate'),holes


class Confirmation:
    def __init__(self):self.reset()
    def reset(self):self.last_direction=None;self.last_received=None;self.key=None;self.count=0
    def update(self,raw,received,now,key):
        age=now-received
        if age<0 or age>MAX_AGE_S:
            self.reset();return dict(raw,status='UNKNOWN',direction=None,reason='stale_frame',age_ms=age*1000)
        if key!=self.key or self.last_received is None or received<=self.last_received or received-self.last_received>MAX_AGE_S:
            self.reset()
        self.key=key
        direction=raw['direction'] if raw['status']=='CANDIDATE' else None
        self.count=self.count+1 if direction is not None and direction==self.last_direction else (1 if direction else 0)
        self.last_direction=direction;self.last_received=received
        status='OBSTACLE' if self.count>=2 else 'PENDING' if direction else raw['status']
        return dict(raw,status=status,direction=direction if status=='OBSTACLE' else None,age_ms=age*1000)


def render(bgr,category,result,backend):
    image=cv2.resize(bgr,(640,480))
    floor=cv2.resize((category==1).astype(np.uint8),(640,480),interpolation=cv2.INTER_NEAREST)>0
    image[floor]=(image[floor]*.6+np.array([210,210,0])*.4).astype(np.uint8)
    corridor=result.get('corridor',{})
    points=corridor.get('points',[])
    if points:
        polygon=np.array([[p[0]*2,p[1]*2] for p in points]+[[p[2]*2,p[1]*2] for p in reversed(points)],np.int32)
        overlay=image.copy();cv2.fillPoly(overlay,[polygon],(200,80,180))
        image=cv2.addWeighted(image,.82,overlay,.18,0)
        color=(200,80,180) if corridor['source']=='ADAPTIVE' else (150,150,150)
        cv2.polylines(image,[polygon],True,color,2)
        cv2.putText(image,corridor['source'],(12,26),cv2.FONT_HERSHEY_SIMPLEX,.55,color,2)
    for box in result['boxes']:
        x,y,w,h=[box[k]*2 for k in ['x','y','w','h']]
        color=(20,20,240) if box.get('primary') else (0,140,255)
        cv2.rectangle(image,(x,y),(x+w,y+h),color,3 if box.get('primary') else 2)
    status=result['status'];direction=result['direction'] or '-'
    canvas=np.zeros((560,640,3),np.uint8);canvas[:480]=image
    color=(0,170,255) if status=='OBSTACLE' else (220,220,220)
    cv2.putText(canvas,status+'  '+direction,(12,510),cv2.FONT_HERSHEY_SIMPLEX,.75,color,2)
    cv2.putText(canvas,backend+' | floor-gap DEMO | not a safe-path decision',(12,542),cv2.FONT_HERSHEY_SIMPLEX,.43,(210,210,210),1)
    return canvas
