"""Image-space straight-lane correction candidate; no body-heading claim."""
import cv2,numpy as np

TTL=.5
LEVELS=('LOW','MEDIUM','HIGH')
LEVEL_BOUNDARIES=(.30,.50)
LEVEL_HYSTERESIS=.03


def geometry(mask):
    if mask.shape!=(240,320):raise ValueError('Mask contract')
    count,labels,stats,_=cv2.connectedComponentsWithStats((mask>0).astype(np.uint8),8)
    candidates=[]
    for index in range(1,count):
        rows=[];centers=[];widths=[]
        for y in range(132,217):
            xs=np.flatnonzero(labels[y]==index)
            if len(xs)>=3:rows.append(y);centers.append((xs[0]+xs[-1])/2);widths.append(xs[-1]-xs[0]+1)
        if len(rows)<16 or np.ptp(rows)<30:continue
        line=np.polyfit(rows,centers,1);error=float(np.median(np.abs(np.polyval(line,rows)-centers)))
        if error>12 or max(widths)>max(112,2.8*float(np.median(widths))):continue
        candidates.append(dict(offset=float((np.polyval(line,min(204,max(rows)))-160)/160),
                               slope=float(line[0]),rows=len(rows),near_y=max(rows),fit_error_px=error))
    if len(candidates)!=1:return dict(status='UNKNOWN',reason='no_near_support' if not candidates else 'multiple_near_paths',direction=None)
    return dict(status='VISIBLE',reason='image_space_only',direction=None,**candidates[0])


class PathPolicy:
    def __init__(self):self.reset()
    def reset(self):
        self.key=None;self.received=None;self.direction=None;self.count=0
        self.active_direction=None;self._clear_level()
    def _clear_level(self):
        self.level='NONE';self.pending_level=None;self.level_count=0
    def _clear_guidance(self):
        self.direction=None;self.count=0;self.active_direction=None;self._clear_level()
    def _update_level(self,magnitude):
        current=LEVELS.index(self.level);desired=current
        while desired<2 and magnitude>=LEVEL_BOUNDARIES[desired]+LEVEL_HYSTERESIS:desired+=1
        while desired>0 and magnitude<=LEVEL_BOUNDARIES[desired-1]-LEVEL_HYSTERESIS:desired-=1
        level=LEVELS[desired]
        if level==self.level:self.pending_level=None;self.level_count=0;return
        self.level_count=self.level_count+1 if self.pending_level==level else 1;self.pending_level=level
        if self.level_count>=2:self.level=level;self.pending_level=None;self.level_count=0
    def update(self,mask,received,now,key):
        if not 0<=now-received<=TTL:self.reset();return dict(status='UNKNOWN',reason='stale_path',direction=None,deviation_level='NONE')
        if key!=self.key or self.received is None or received-self.received>TTL:self.reset()
        if self.received is not None and received<=self.received:
            self.reset();return dict(status='UNKNOWN',reason='duplicate_or_reversed_frame',direction=None,deviation_level='NONE')
        self.key=key;self.received=received;result=geometry(mask)
        if result['status']=='UNKNOWN':self._clear_guidance();return dict(result,deviation_level='NONE')
        offset=result['offset']
        if not np.isfinite(offset):
            self._clear_guidance();return dict(status='UNKNOWN',reason='invalid_offset',direction=None,deviation_level='NONE')
        magnitude=abs(offset);side='LEFT' if offset<0 else 'RIGHT'
        # Only a confirmed correction can persist in the .10..16 band.
        continuing=self.active_direction==side and magnitude>.10
        direction=side if magnitude>.16 or continuing else 'CENTER'
        self.count=min(3,self.count+1) if direction==self.direction else 1;self.direction=direction
        if direction=='CENTER':
            self.active_direction=None;self._clear_level()
        elif continuing:
            if magnitude<=.16:
                self.level='LOW';self.pending_level=None;self.level_count=0
            else:self._update_level(magnitude)
        else:
            self.active_direction=None;self._clear_level()
            if self.count>=3:
                self.active_direction=direction
                self.level='LOW' if magnitude<.30 else 'MEDIUM' if magnitude<.50 else 'HIGH'
        return dict(result,status='CONFIRMED' if self.count>=3 else 'PENDING',direction=direction,
                    confirmation_count=self.count,deviation_level=self.level)


def correction(path,path_received,floor,floor_received,now,camera_fixed=False,mapping_confirmed=False,same_source=True):
    if not camera_fixed:return None,'camera_orientation_unconfirmed'
    if not mapping_confirmed:return None,'wearing_sides_unconfirmed'
    if not same_source:return None,'source_mismatch'
    if not 0<=now-floor_received<=TTL:return None,'stale_obstacle_result'
    if floor['status']!='NO_CANDIDATE':return None,'obstacle_or_unknown_priority'
    if not 0<=now-path_received<=TTL:return None,'stale_path'
    if path['status']!='CONFIRMED':return None,'path_unconfirmed'
    if path['direction']=='CENTER':return None,'aligned_no_pulse'
    return {'LEFT':'left','RIGHT':'right'}[path['direction']],'image_space_correction_only'
