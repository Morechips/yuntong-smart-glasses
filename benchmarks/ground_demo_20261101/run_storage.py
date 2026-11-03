"""Bounded per-run logs and rolling timing samples, with cumulative counters."""
from collections import Counter,deque
import json,os
import numpy as np


class RunStorage:
    def __init__(self,directory,max_bytes=8*1024*1024,backups=3,window=2048):
        self.directory=directory;self.max_bytes=max_bytes;self.backups=backups
        self.path=directory/'events.jsonl';self.file=self.path.open('ab');self.size=0
        self.counts=Counter();self.age=deque(maxlen=window);self.post=deque(maxlen=window);self.model=deque(maxlen=window)
        self.window=window
    def add(self,row):
        data=(json.dumps(row,ensure_ascii=False)+'\n').encode('utf-8')
        if self.size and self.size+len(data)>self.max_bytes:
            self.file.close()
            oldest=self.directory/('events.%d.jsonl'%self.backups)
            if oldest.exists():oldest.unlink()
            for index in range(self.backups-1,0,-1):
                source=self.directory/('events.%d.jsonl'%index)
                if source.exists():source.replace(self.directory/('events.%d.jsonl'%(index+1)))
            self.path.replace(self.directory/'events.1.jsonl')
            self.file=self.path.open('ab');self.size=0
        self.file.write(data);self.file.flush();self.size+=len(data);self.counts[row['type']]+=1
        if row['type']=='result':
            self.age.append(row['result']['age_ms']);self.post.append(row['result'].get('postprocess_ms',0))
            value=row['timing'].get('model_readback_ms')
            if value is not None:self.model.append(value)
    def statistics(self):
        percentile=lambda values:float(np.percentile(values,95)) if values else None
        return dict(results=self.counts['result'],haptic_acknowledgments=self.counts['haptic'],
                    receive_to_result_p95_ms=percentile(self.age),postprocess_p95_ms=percentile(self.post),
                    model_readback_p95_ms=percentile(self.model),timing_sample_count=len(self.age),
                    timing_statistics_scope='Latest at most %d results; counts are cumulative'%self.window)
    def checkpoint(self,**state):
        temp=self.directory/'current_status.json.tmp'
        temp.write_text(json.dumps(dict(self.statistics(),**state),ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temp,self.directory/'current_status.json')
    def close(self):self.file.close()
