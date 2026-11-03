"""Finish offline synthesis; preserve original single-region voice exactly."""
from pathlib import Path
import hashlib,json,shutil,wave
import numpy as np

def main():
    base=Path(__file__).resolve().parent;folder=base/'audio_corridor';raw=folder/'raw'
    raw.mkdir(exist_ok=False);rows=[]
    for phrase in json.loads((base/'speech_phrases.json').read_text(encoding='utf-8')):
        key=phrase['direction'];path=folder/(key+'.wav');shutil.move(path,raw/path.name)
        if key in ['LEFT','CENTER','RIGHT']:shutil.copy2(base/'audio_clear'/path.name,path)
        else:
            with wave.open(str(raw/path.name),'rb') as source:
                params=source.getparams();samples=np.frombuffer(source.readframes(source.getnframes()),'<i2').astype(np.float64)/32768
            boosted=np.tanh(samples*3);peak=float(np.max(np.abs(boosted)))
            if peak:boosted*=.90/peak
            body=np.rint(boosted*32767).astype('<i2').tobytes()
            with wave.open(str(path),'wb') as target:target.setparams(params);target.writeframes(body)
        with wave.open(str(path),'rb') as source:
            duration=source.getnframes()/source.getframerate();size=source.getnframes()*2
        if duration>3.5:raise ValueError('Clip too long: '+key)
        rows.append(dict(direction=key,text=phrase['text'],duration_s=duration,pcm_bytes=size,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (folder/'manifest.json').write_text(json.dumps(dict(scope='Offline synthesis only; three original single-region WAVs unchanged; combinations soft-limited',clips=rows),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rows,ensure_ascii=False))

if __name__=='__main__':main()
