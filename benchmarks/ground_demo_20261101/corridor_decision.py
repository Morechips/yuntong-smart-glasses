"""Image-space corridor attention, independent regional confirmation; not routing."""
import math,time
import cv2
import numpy as np
from demo_core import candidates,Confirmation,MAX_AGE_S

REGIONS=('LEFT','CENTER','RIGHT')
Y0,Y1=125,234
YS=np.arange(Y0,Y1)
# Promotion is explicit after development replay, timing and positioned live checks.
DEFAULT_DECISION_MODE='legacy'


def fixed_corridor(reason):
    return np.full(len(YS),160.),np.linspace(48.,160.,len(YS)),dict(source='FIXED_FALLBACK',reason=reason)


def _enough(rows):
    return len(rows)>=40 and np.ptp(rows)>=60 and (rows<179).sum()>=10 and (rows>=179).sum()>=10


def estimate_axis(category,boxes):
    floor=(category==1).astype(np.uint8)
    n,labels,stats,_=cv2.connectedComponentsWithStats(floor,8)
    if n<2:return None,'insufficient_floor',{}
    main=labels==(1+np.argmax(stats[1:,cv2.CC_STAT_AREA]))
    # Outer boundaries include internal gaps: never choose the largest free gap.
    present=main[YS];counts=present.sum(1)
    left=present.argmax(1).astype(float)
    right=(319-present[:,::-1].argmax(1)).astype(float)
    valid=(counts>=16)&(right-left>=32)
    for box in boxes:
        occupied=(YS>=box['y'])&(YS<box['y']+box['h'])
        # An INTERNAL hole with floor on BOTH flanks leaves the outer edges
        # usable. Discard only cut-in/poorly supported boundary rows.
        truncated=(box['x']-left<8)|(right-(box['x']+box['w']-1)<8)
        valid&=~(occupied&truncated)
    span=right-left+1
    valid&=(counts>=span*.5)
    rows=YS[valid]
    if not _enough(rows):return None,'too_few_clean_rows',dict(clean_rows=int(len(rows)),
        row_span=int(np.ptp(rows)) if len(rows) else 0,far_rows=int((rows<179).sum()),near_rows=int((rows>=179).sum()))
    # Fill missing/obstacle rows before a 9-row median, so obstacle cut-ins do
    # not contaminate their neighbouring clean boundary rows.
    smooth=[]
    for values in [left,right]:
        filled=np.interp(YS,rows,values[valid])
        windows=np.lib.stride_tricks.sliding_window_view(np.pad(filled,4,mode='edge'),9)
        smooth.append(np.median(windows,axis=1))
    centers=(smooth[0]+smooth[1])/2
    line=np.polyfit(rows,centers[valid],1)
    residual=np.abs(centers-np.polyval(line,YS))
    limit=max(6.,2.5*float(np.median(residual[valid])))
    valid&=residual<=limit;rows=YS[valid]
    if not _enough(rows):return None,'inconsistent_outer_edges',dict(clean_rows=int(len(rows)))
    line=np.polyfit(rows,centers[valid],1)
    error=float(np.median(np.abs(centers[valid]-np.polyval(line,rows))))
    if error>12:return None,'unstable_axis_fit',dict(clean_rows=int(len(rows)),fit_error_px=error)
    center=np.clip(np.polyval(line,YS),16,304)
    widths=(smooth[1]-smooth[0]+1)*.5
    width=np.clip(np.polyval(np.polyfit(rows,widths[valid],1),YS),48,160)
    return (center,width),'outer_edges_fit',dict(clean_rows=int(len(rows)),fit_error_px=error)


def corridor_geometry(center,width,metadata):
    left=np.clip(np.rint(center-width/2),16,304).astype(int)
    right=np.clip(np.rint(center+width/2),16,304).astype(int)
    points=[[int(left[i]),int(YS[i]),int(right[i])] for i in range(0,len(YS),9)]
    if points[-1][1]!=int(YS[-1]):points.append([int(left[-1]),int(YS[-1]),int(right[-1])])
    return left,right,dict(metadata,points=points,center_x=float(center[len(YS)//2]),width_px=float(width[len(YS)//2]))


class CorridorDecision:
    def __init__(self):self.reset()
    def reset(self):
        self.previous=None;self.key=None;self.valid_axis_frames=0;self.axis=None
        self.counts={r:0 for r in REGIONS}
    def update(self,category,received,now,key):
        started=time.perf_counter()
        age=now-received
        if not 0<=age<=MAX_AGE_S:
            self.reset()
            return self._unknown('stale_frame',age*1000)
        if key!=self.key or self.previous is None or received<=self.previous or received-self.previous>MAX_AGE_S:self.reset()
        dt=received-self.previous if self.previous is not None else 0
        self.key=key;self.previous=received
        raw,holes=candidates(category)
        if raw['status']=='UNKNOWN':
            self.reset();return self._unknown(raw['reason'],age*1000)
        fitted,reason,details=estimate_axis(category,raw['boxes'])
        if fitted is None:
            self.valid_axis_frames=0;self.axis=None
            center,width,meta=fixed_corridor(reason)
        else:
            self.valid_axis_frames+=1
            if self.axis is None:self.axis=fitted
            else:
                alpha=1-math.exp(-dt/.25)
                self.axis=tuple(old+(new-old)*alpha for old,new in zip(self.axis,fitted))
            if self.valid_axis_frames<3:center,width,meta=fixed_corridor('axis_warmup')
            else:center,width=self.axis;meta=dict(source='ADAPTIVE',reason=reason)
        meta.update(details);left,right,geometry=corridor_geometry(center,width,meta)
        n,labels,stats,_=cv2.connectedComponentsWithStats(holes,8)
        xs=np.arange(320)[None,:]
        masks={'LEFT':xs<left[:,None],'CENTER':(xs>=left[:,None])&(xs<right[:,None]),'RIGHT':xs>=right[:,None]}
        overlap={r:np.bincount(labels[YS][masks[r]],minlength=n) for r in REGIONS}
        boxes=[];present=set();scores={r:(-1,0) for r in REGIONS}
        for index in range(1,n):
            x,y,w,h,area=map(int,stats[index])
            if area<80 or w<8 or h<10:continue
            regions=[r for r in REGIONS if overlap[r][index]>=max(24,area*.1)]
            box=dict(x=x,y=y,w=w,h=h,area=area,regions=regions,
                     region_pixels={r:int(overlap[r][index]) for r in regions})
            boxes.append(box);present.update(regions)
            for r in regions:scores[r]=max(scores[r],(y+h-1,area))
        boxes.sort(key=lambda box:box['area'],reverse=True)
        for r in REGIONS:self.counts[r]=self.counts[r]+1 if r in present else 0
        confirmed=[r for r in REGIONS if self.counts[r]>=2]
        direction='CENTER' if 'CENTER' in confirmed else max(confirmed,key=lambda r:scores[r]) if confirmed else None
        for box in boxes:box['primary']=direction is not None and direction in box['regions']
        elapsed=(time.perf_counter()-started)*1000
        final_age=age*1000+elapsed
        if final_age>MAX_AGE_S*1000:
            self.reset();return dict(self._unknown('stale_after_decision',final_age),postprocess_ms=elapsed)
        return dict(status='OBSTACLE' if confirmed else 'PENDING' if present else 'NO_CANDIDATE',direction=direction,
                    boxes=boxes,reason='predicted_floor_gap' if boxes else 'no_gap_above_demo_gate',
                    regions=[r for r in REGIONS if r in present],confirmed_regions=confirmed,
                    corridor=geometry,decision_mode='adaptive',age_ms=final_age,postprocess_ms=elapsed)
    @staticmethod
    def _unknown(reason,age_ms):
        return dict(status='UNKNOWN',direction=None,boxes=[],regions=[],confirmed_regions=[],
                    corridor=dict(source='UNKNOWN',reason=reason,points=[]),reason=reason,age_ms=age_ms,decision_mode='adaptive')


class LegacyDecision:
    def __init__(self):self.gate=Confirmation()
    def reset(self):self.gate.reset()
    def update(self,category,received,now,key):
        started=time.perf_counter();raw,_=candidates(category)
        elapsed=(time.perf_counter()-started)*1000
        result=self.gate.update(raw,received,now+elapsed/1000,key)
        return dict(result,decision_mode='legacy',postprocess_ms=elapsed,
                    confirmed_regions=[result['direction']] if result['status']=='OBSTACLE' else [])


def decision_engine(mode):
    if mode=='adaptive':return CorridorDecision()
    if mode=='legacy':return LegacyDecision()
    raise ValueError('Unknown decision mode: '+str(mode))
