"""Offline delivery checks; standard library only, no device or network IO."""
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse
import ast
import hashlib
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def public_files():
    for path in ROOT.rglob('*'):
        rel = path.relative_to(ROOT)
        if not path.is_file() or any(p in ('.git', '.venv', '__pycache__', 'outputs', 'datasets') for p in rel.parts):
            continue
        if path.name.endswith('.local.json') or re.search(r'/runs/live_[^/]+/', '/' + rel.as_posix()):
            continue
        yield path


def verify():
    errors, stats = [], dict()
    manifest = json.loads((ROOT / 'submission/MANIFEST.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        path = ROOT / item['path']
        if not path.resolve().is_relative_to(ROOT):
            errors.append('Unsafe manifest path: ' + item['path'])
        elif not path.is_file():
            errors.append('Missing: ' + item['path'])
        elif hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            errors.append('SHA256 mismatch: ' + item['path'])
    files = list(public_files())
    indexed = {item['path'] for item in manifest['files']}
    exempt = {'submission/MANIFEST.json', 'submission/validation.json'}
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith('3D打印文件/') and (path.suffix in ('.py', '.js') or path.name in ('parameters.json', 'physics_spec.json')):
            errors.append('CAD generator source excluded by submission scope: ' + rel)
        if rel not in indexed and rel not in exempt:
            errors.append('Unlisted public file: ' + rel)
        if path.stat().st_size > 50 * 1024**2:
            errors.append('Exceeds package 50MiB per-file policy: ' + rel)
        if path.name in ('sdkconfig', 'sdkconfig.local', 'ubuntu密码.txt', 'ssh-askpass-board.cmd') or path.suffix in ('.pem', '.key', '.pfx', '.p12'):
            errors.append('Disallowed local/credential file: ' + rel)
        if path.suffix == '.py':
            try:
                ast.parse(path.read_text(encoding='utf-8-sig'), filename=rel)
            except (SyntaxError, UnicodeError) as exc:
                errors.append('Python syntax: ' + rel + ': ' + str(exc))
        if path.suffix == '.json':
            try:
                json.loads(path.read_text(encoding='utf-8-sig'))
            except (ValueError, UnicodeError) as exc:
                errors.append('JSON: ' + rel + ': ' + str(exc))
        if path.suffix == '.md':
            body = path.read_text(encoding='utf-8-sig')
            for match in re.finditer(r'!?\[[^\]]*\]\(([^\n]+?)\)', body):
                target = match.group(1).strip()
                if target.startswith('<') and target.endswith('>'):
                    target = target[1:-1]
                parsed = urlsplit(target)
                if parsed.scheme or target.startswith(('#', '//')):
                    continue
                target = unquote(parsed.path)
                if target and not (path.parent / target).exists():
                    errors.append('Broken local link in ' + rel + ': ' + target)
        if path.suffix in ('.py', '.c', '.h', '.md', '.json', '.txt', '.ps1', '.defaults', '.yml') or path.name.startswith('sdkconfig'):
            body = path.read_text(encoding='utf-8-sig', errors='replace')
            if re.search(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----', body):
                errors.append('Private key marker: ' + rel)
            if re.search(r'(?m)^CONFIG_CAM_WIFI_PASSWORD="[^"\n]+"', body):
                errors.append('Nonempty Wi-Fi password configuration: ' + rel)
            if re.search(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-[A-Za-z0-9]{32,})\b', body):
                errors.append('Credential token pattern: ' + rel)
    inputs = json.loads((ROOT / 'benchmarks/ground_demo_20261101/runs/compile_01/inputs.json').read_text(encoding='utf-8'))
    for row in inputs['samples']:
        source = ROOT / row['source'].replace('\\', '/')
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != row['source_sha256']:
            errors.append('Fixed JPEG missing/changed: ' + row['sample_id'])
        expected = ROOT / 'benchmarks/ground_demo_20261101/runs/compile_01/emulator' / (row['sample_id'] + '.npy')
        if not expected.is_file():
            errors.append('Fixed emulator output missing: ' + row['sample_id'])
    for required in ('README.md', 'RIGHTS.md', 'config/device.json.example', 'firmware/esp32s3/main/main.c',
                     'benchmarks/ground_route_screen_20261031/runs/round02_20261101/floor_only/model.onnx',
                     'benchmarks/ground_demo_20261101/runs/compile_01/work/floor_demo.bin'):
        if not (ROOT / required).is_file():
            errors.append('Required file missing: ' + required)
    stats.update(files=len(files), total_bytes=sum(p.stat().st_size for p in files),
                 largest_file_bytes=max(p.stat().st_size for p in files), fixed_jpegs=len(inputs['samples']),
                 checked_manifest_files=len(manifest['files']))
    return dict(passed=not errors, errors=errors, stats=stats,
                checks=['SHA256 inventory', 'required files and model fixtures', 'Python AST', 'JSON parse',
                        'local Markdown links', 'credential filename/key/token/Wi-Fi patterns', 'package per-file size policy'],
                scope='Local static checks only; not exhaustive security review or hardware verification')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    result = verify()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
