"""Run real ONNX inference on bundled development JPEGs. No device IO."""
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'benchmarks/ground_demo_20261101'
sys.path.insert(0, str(DEMO))


def main():
    import cv2
    import numpy as np
    from backends import OnnxBackend, MODEL
    from demo_core import candidates

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/offline_demo')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    samples = json.loads((DEMO / 'runs/compile_01/inputs.json').read_text(encoding='utf-8'))['samples']
    backend = OnnxBackend()
    rows, tiles = [], []
    try:
        for index, sample in enumerate(samples):
            source = ROOT / sample['source'].replace('\\', '/')
            jpeg = source.read_bytes()
            if hashlib.sha256(jpeg).hexdigest() != sample['source_sha256']:
                raise ValueError('Development JPEG hash mismatch: ' + sample['sample_id'])
            category, timing = backend.infer(jpeg, index)
            result, _ = candidates(category)
            image = cv2.resize(cv2.imdecode(np.frombuffer(jpeg, np.uint8), 1), (320, 240))
            tint = image.copy()
            tint[category == 1] = (180, 210, 30)
            image = cv2.addWeighted(image, .6, tint, .4, 0)
            for box in result['boxes']:
                x, y, w, h = [box[k] for k in ('x', 'y', 'w', 'h')]
                cv2.rectangle(image, (x, y), (x+w, y+h), (20, 20, 240), 2)
            image = cv2.copyMakeBorder(image, 28, 0, 0, 0, cv2.BORDER_CONSTANT, value=(25, 25, 25))
            label = sample['sample_id'] + ' ' + result['status'] + ' ' + (result.get('direction') or '-')
            cv2.putText(image, label, (5, 19), cv2.FONT_HERSHEY_SIMPLEX, .4, (255, 255, 255), 1)
            ok, encoded = cv2.imencode('.jpg', image)
            if not ok:
                raise RuntimeError('JPEG encoding failed')
            (args.output / (sample['sample_id'] + '.jpg')).write_bytes(encoded.tobytes())
            if len(tiles) < 8:
                tiles.append(image)
            rows.append(dict(sample_id=sample['sample_id'], result=result, timing=timing))
        preview = np.vstack([np.hstack(tiles[:4]), np.hstack(tiles[4:8])])
        ok, encoded = cv2.imencode('.jpg', preview)
        if not ok:
            raise RuntimeError('Preview encoding failed')
        (args.output / 'preview.jpg').write_bytes(encoded.tobytes())
        report = dict(scope='Independent single-image development inference; NOT temporal confirmation, accuracy or X5 timing',
                      model_sha256=hashlib.sha256(MODEL.read_bytes()).hexdigest(), samples=len(rows),
                      device_io=False, feedback_sent=False, rows=rows)
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(samples=len(rows), output=str(args.output), device_io=False), ensure_ascii=False))
    finally:
        backend.close()


if __name__ == '__main__':
    main()
