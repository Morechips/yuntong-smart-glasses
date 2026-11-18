"""Authenticated LAN adapter for the existing, silent RDK runtime. No autostart."""
import argparse
from collections import OrderedDict
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time

UNIT = 'yuntong-standalone-demo.service'
WEAR_STATES = {'wait_static', 'preparing', 'searching', 'wait_front', 'manual_pause',
               'rapid_turn', 'off_forward', 'imu_stale', 'recovery', 'new_frames',
               'ready', 'reference_expired', 'image_unavailable', 'unavailable'}


class ApiError(Exception):
    def __init__(self, message, code=409):
        self.code = code
        super().__init__(message)


def read_json(path):
    try:
        data = path.read_bytes()
        if len(data) > 65536:
            raise ApiError('设备状态文件过大，未读取。', 503)
        return json.loads(data)
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        raise ApiError('设备状态暂不可读取，请稍后重试。', 503) from exc


def normalize(service, current, wear, now):
    active = service.get('ActiveState') in ('active', 'activating', 'deactivating')
    if service.get('ActiveState') == 'deactivating':
        phase = 'stopping'
    elif active:
        phase = 'running' if current.get('phase') == 'running' else 'starting'
    else:
        phase = 'failed' if service.get('ActiveState') == 'failed' or current.get('error') else 'idle'
    timestamp = wear.get('rdk_monotonic')
    fresh = isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and 0 <= now - timestamp <= 2
    state = wear.get('state', 'unavailable') if active and fresh and wear.get('active') is True else 'unavailable'
    if state not in WEAR_STATES:
        state = 'unavailable'
    if active and wear.get('mode') not in (None, 'shadow'):
        raise ApiError('当前任务不是静默观察，请先在设备端结束原任务。')
    return dict(schema='yuntong-mobile-v1', deviceName='云瞳智能眼镜', running=active,
                phase=phase, stale=active and (not fresh or wear.get('active') is not True), mode='shadow',
                wear=dict(state=state, biasReady=active and fresh and wear.get('active') is True and wear.get('bias_ready') is True),
                output='none')


class Runtime:
    def __init__(self, root, run=Path('/run/yuntong-demo/current'), archives=None):
        self.root = Path(root).resolve()
        self.run = Path(run)
        self.archives = Path(archives) if archives else Path.home() / 'yuntong-mobile-records'
        if not (self.root / 'democtl.py').is_file() or not (self.root / 'runtime.py').is_file():
            raise ValueError('runtime 必须指向已安装的佩戴运行包目录')

    def archive_previous(self):
        if not self.run.exists():
            return
        files = list(self.run.rglob('*'))
        if self.run.is_symlink() or any(p.is_symlink() for p in files):
            raise ApiError('旧记录目录包含链接，请先在电脑端回收记录。')
        if sum(p.stat().st_size for p in files if p.is_file()) > 32 * 1024 * 1024:
            raise ApiError('旧记录较大，请先从电脑回收记录，再开始新一轮。')
        if files:
            self.archives.mkdir(parents=True, exist_ok=True)
            destination = self.archives / ('before-start-' + secrets.token_hex(8))
            shutil.copytree(self.run, destination)

    def service(self):
        p = subprocess.run(['systemctl', 'show', UNIT, '--property=ActiveState', '--property=SubState', '--property=MainPID'],
                           capture_output=True, text=True, timeout=4)
        if p.returncode:
            raise ApiError('无法读取板端任务，请检查 systemd 服务。', 503)
        result = dict(line.split('=', 1) for line in p.stdout.splitlines() if '=' in line)
        if result.get('ActiveState') not in ('active', 'activating', 'deactivating', 'inactive', 'failed'):
            raise ApiError('无法确认板端任务状态。', 503)
        pid = result.get('MainPID', '0')
        if result['ActiveState'] in ('active', 'activating', 'deactivating'):
            if not pid.isdigit() or int(pid) <= 0:
                raise ApiError('板端任务正在切换，请稍后重试。', 503)
            try:
                argv = Path('/proc') / pid / 'cmdline'
                args = argv.read_bytes().decode().split('\0')
                if str(self.root / 'runtime.py') not in args:
                    raise ApiError('另一版本正在运行，请先在原入口停止，再连接当前版本。')
            except OSError as exc:
                raise ApiError('任务状态正在变化，请稍后重试。', 503) from exc
        return result

    def status(self):
        service = self.service()
        return normalize(service, read_json(self.run / 'current_status.json'),
                         read_json(self.run / 'wear_status.json'), time.monotonic())

    def command(self, action, ident):
        before = self.status()
        if action == 'start' and before['running']:
            raise ApiError('设备任务已经运行，不会重复启动。')
        if action in ('static', 'front', 'pause'):
            if not before['running'] or before['stale'] or before['phase'] != 'running':
                raise ApiError('请先启动设备，并等待实时状态恢复。')
            if action == 'front' and (not before['wear']['biasReady'] or before['wear']['state'] in ('preparing', 'searching')):
                raise ApiError('请先完成静置准备，再确认正前方。')
            # Same lock and atomic file protocol as democtl. Ack must match our UUID.
            import fcntl
            try:
                with open('/tmp/yuntong-standalone-control.lock', 'a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    target = self.run / 'wear_command.json'
                    temporary = self.run / ('mobile-command-' + ident + '.tmp')
                    temporary.write_text(json.dumps(dict(id=ident, action=action)), encoding='utf-8')
                    os.replace(temporary, target)
            except BlockingIOError as exc:
                raise ApiError('设备正在处理另一条指令，请稍后重试。') from exc
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                ack = read_json(self.run / 'wear_status.json').get('command') or {}
                if ack.get('id') == ident:
                    if ack.get('ok') is not True:
                        raise ApiError('设备未接受该操作，请按当前状态重新准备。')
                    break
                time.sleep(.05)
            else:
                raise ApiError('指令已发送但未收到确认，请刷新状态，不要连续重复操作。', 504)
        elif action == 'stop' and not before['running']:
            return dict(status=before, message='设备任务已停止。')
        else:
            args = [sys.executable, '-B', str(self.root / 'democtl.py'), action]
            if action == 'start':
                self.archive_previous()
                args += ['--seconds', '600', '--imu-mode', 'shadow']
            try:
                p = subprocess.run(args, cwd=self.root, capture_output=True, text=True, timeout=42)
            except subprocess.TimeoutExpired as exc:
                raise ApiError('操作等待超时，结果未确认，请刷新设备状态。', 504) from exc
            if p.returncode:
                raise ApiError('设备未完成操作，请检查板端运行日志，再刷新状态。', 503)
        after = self.status()
        if action == 'stop' and after['running']:
            raise ApiError('尚未确认设备停止，请稍后刷新状态。', 504)
        if action == 'start' and not after['running']:
            raise ApiError('设备启动未成功，请检查板端日志。', 503)
        messages = dict(start='设备已启动静默观察，请继续完成佩戴准备。', stop='设备已确认停止。',
                        static='设备已接受静置准备，请放稳眼镜。', front='设备已接受前方设置，请等待新的有效画面。', pause='设备已接受暂停提示。')
        return dict(status=after, message=messages[action])


class Api:
    def __init__(self, runtime, token):
        self.runtime, self.token = runtime, token
        self.lock = threading.Lock()
        self.cache = OrderedDict()

    def execute(self, action, ident):
        if action not in ('start', 'stop', 'static', 'front', 'pause'):
            raise ApiError('不支持此操作。', 400)
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9-]{36}', ident):
            raise ApiError('请求标识无效。', 400)
        if not self.lock.acquire(timeout=.2):
            raise ApiError('另一条操作正在执行，请等待回执。')
        try:
            if ident in self.cache:
                cached_action, result = self.cache[ident]
                if cached_action != action:
                    raise ApiError('重复标识不能用于另一条操作。', 400)
                if isinstance(result, ApiError):
                    raise result
                # A replay must not return an outdated running/ready snapshot.
                return dict(status=self.runtime.status(), message='该指令已经处理，已刷新当前状态。')
            try:
                result = self.runtime.command(action, ident)
            except ApiError as exc:
                result = exc
            except Exception as exc:
                result = ApiError('设备操作异常，结果未确认，请刷新状态。', 503)
            self.cache[ident] = (action, result)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
            if isinstance(result, ApiError):
                raise result
            return result
        finally:
            self.lock.release()


def handler(api):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'YuntongMobile/0.1'
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            pass  # Tokens and bodies are never logged.

        def reply(self, code, value):
            data = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True

        def dispatch(self, method):
            try:
                auth = self.headers.get('Authorization', '')
                if not hmac.compare_digest(auth.encode(), ('Bearer ' + api.token).encode()):
                    raise ApiError('连接密钥不正确，请重新填写。', 401)
                if self.headers.get('Origin'):
                    raise ApiError('该接口仅接受手机原生连接。', 403)
                if method == 'GET' and self.path == '/api/v1/status':
                    self.reply(200, api.runtime.status())
                elif method == 'POST' and self.path == '/api/v1/command':
                    if self.headers.get('Transfer-Encoding'):
                        raise ApiError('不支持分块请求。', 400)
                    if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        raise ApiError('需要 JSON 请求。', 415)
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 4096:
                        raise ApiError('请求长度不合法。', 413)
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ApiError('请求不完整。', 400)
                    body = json.loads(raw)
                    if not isinstance(body, dict) or set(body) != {'action', 'requestId'} or not isinstance(body['action'], str):
                        raise ApiError('请求字段不正确。', 400)
                    self.reply(200, api.execute(body['action'], body['requestId']))
                else:
                    raise ApiError('接口不存在。', 404)
            except ApiError as exc:
                self.reply(exc.code, dict(error=str(exc)))
            except (ValueError, TypeError):
                self.reply(400, dict(error='请求格式不正确。'))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.reply(503, dict(error='设备服务暂不可用，请稍后重试。'))

        def do_GET(self):
            self.dispatch('GET')

        def do_POST(self):
            self.dispatch('POST')
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--token-file', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'posix' or os.getuid() == 0:
        parser.error('请在 RDK 上使用普通运行用户启动，不要以 root 运行。')
    if args.token_file.exists():
        token = args.token_file.read_text().strip()
    else:
        token = secrets.token_urlsafe(24)
        fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(token)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', token):
        parser.error('密钥必须为 16—128 位字母、数字、下划线或连字符。')
    api = Api(Runtime(args.runtime, archives=args.token_file.resolve().parent / 'records'), token)
    server = ThreadingHTTPServer((args.host, args.port), handler(api))
    print(f'连接服务监听 {args.host}:{args.port}；请在 App 填写 RDK 的局域网 IP。', flush=True)
    print('连接密钥：' + token, flush=True)
    print('启动此服务不会启动图流。手机显式开始后，静默任务最多运行 600 秒。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
