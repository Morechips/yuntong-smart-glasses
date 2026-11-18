"""RDK companion endpoint for the existing board-local shadow runtime."""

import argparse
import asyncio
import base64
from collections import OrderedDict
from contextlib import suppress
import hmac
import json
from pathlib import Path
import ssl
import sys
import time
from uuid import UUID, uuid4
from subprocess import TimeoutExpired

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bridge.server import Api, ApiError, Runtime, read_json

SCHEMA = "yuntong-rdk-link-v1"
CAPABILITIES = ["navigation.receive", "device.status", "device.command", "guidance.shadow",
                "media.preview", "media.capture", "fall.confirmed"]
COMMANDS = {"start": "start", "stop": "stop", "prepare": "static", "front.confirm": "front"}
WEAR_LABELS = {
    "wait_static": "等待静置准备", "preparing": "准备中，请放稳眼镜",
    "searching": "正在等待连续稳定", "wait_front": "等待确认正前方",
    "manual_pause": "佩戴提示已暂停", "rapid_turn": "转动较快，等待稳定",
    "off_forward": "偏离参考前方", "imu_stale": "姿态数据暂不可用",
    "recovery": "等待回正与稳定", "new_frames": "等待新的画面",
    "ready": "姿态与画面就绪", "reference_expired": "需要重新确认前方",
    "image_unavailable": "画面或姿态关联暂不可用",
}
OBSTACLES = {"LEFT": "画面左侧", "CENTER": "画面中央", "RIGHT": "画面右侧"}


def send(ws, kind, **fields):
    return ws.send(json.dumps(dict(schema=SCHEMA, type=kind, **fields), ensure_ascii=False))


def decode(raw):
    if not isinstance(raw, str):
        raise ValueError("JSON text expected")
    packet = json.loads(raw)
    if not isinstance(packet, dict) or packet.get("schema") != SCHEMA:
        raise ValueError("Unexpected protocol")
    return packet


class Navigation:
    def __init__(self):
        self.current = None
        self.route = None
        self.location_at = None
        self.ended = OrderedDict()

    def retire(self, session):
        self.ended[session] = None
        if len(self.ended) > 128:
            self.ended.popitem(last=False)

    def accept(self, packet):
        kind = packet.get("type")
        phase = packet.get("phase")
        session = packet.get("sessionId")
        route, sequence = packet.get("routeVersion"), packet.get("sequence")
        phases = {
            "session.start": {"active"}, "session.active": {"active"},
            "session.paused": {"paused"}, "session.ended": {"ended"},
            "route.replace": {"active", "paused"},
            "navigation.snapshot": {"active", "paused", "ended"},
            "navigation.location": {"active", "paused"}, "navigation.progress": {"active"},
            "user.action": {"active", "paused"},
        }
        if (not isinstance(kind, str) or not isinstance(phase, str)
                or kind not in phases or phase not in phases[kind] or packet.get("source") != "amap"):
            return False
        if not isinstance(session, str) or not 1 <= len(session) <= 100 or session in self.ended:
            return False
        if type(route) is not int or type(sequence) is not int or route < 1 or sequence < 1:
            return False
        previous = self.current
        if kind == "user.action" and packet.get("action") not in {
            "location.unavailable", "route.recalculating", "destination.nearby",
        }:
            return False
        if previous is None or session != previous["sessionId"]:
            if kind not in {"session.start", "navigation.snapshot"}:
                return False
            if previous:
                self.retire(previous["sessionId"])
        else:
            if route < previous["routeVersion"] or sequence <= previous["sequence"]:
                return False
            if route > previous["routeVersion"] and kind not in {"route.replace", "navigation.snapshot"}:
                return False
            if kind in {"navigation.location", "navigation.progress", "route.replace"} and phase != previous["phase"]:
                return False
        if phase == "ended":
            self.retire(session)
            self.route = None
            self.location_at = None
        elif kind in {"session.start", "route.replace", "navigation.snapshot"}:
            route_payload = packet.get("route")
            self.route = route_payload if isinstance(route_payload, dict) else None
        if kind in {"session.start", "user.action"}:
            self.location_at = None
        if kind in {"navigation.location", "navigation.snapshot"}:
            location = packet.get("location")
            lat = location.get("latitude") if isinstance(location, dict) else None
            lon = location.get("longitude") if isinstance(location, dict) else None
            self.location_at = (time.monotonic() if type(lat) in (int, float) and -90 <= lat <= 90
                                and type(lon) in (int, float) and -180 <= lon <= 180 else None)
        self.current = packet
        return True


class Board:
    def __init__(self, runtime):
        self.runtime = runtime
        self.actions = Api(runtime, "")
        self.owned_pid = None

    def status(self):
        status = self.runtime.status()
        if status["phase"] != "running":
            labels = {"idle": "眼镜观察未启动", "failed": "眼镜观察任务异常",
                      "starting": "眼镜观察正在启动", "stopping": "眼镜观察正在停止"}
            return labels[status["phase"]], "等待感知结果", None
        wear = read_json(self.runtime.run / "wear_status.json")
        now = time.monotonic()
        stamp = wear.get("rdk_monotonic")
        if (status["stale"] or wear.get("mode") != "shadow" or wear.get("active") is not True
                or type(stamp) not in (int, float) or not 0 <= now - stamp <= 2):
            return "眼镜状态暂不可用", "等待新的感知结果", None
        if (wear.get("state") == "wait_static" and
                wear.get("message", "").startswith("本次未找到合适窗口")):
            return "静置未通过，请放稳眼镜后重试", wear["message"], None
        label = WEAR_LABELS.get(wear.get("state"), "等待眼镜状态")
        vision = wear.get("vision") or {}
        received = vision.get("received")
        if type(received) not in (int, float) or not 0 <= now - received < .5:
            return label, "等待新的感知结果", None
        direction = OBSTACLES.get(vision.get("direction"))
        if vision.get("floor_status") == "OBSTACLE" and direction:
            cue = f"{direction}检测到障碍候选，请停下确认。"
            # Shadow reports the candidate. A prompt requires a current ready pose.
            return label, cue, (cue, received) if wear.get("state") == "ready" else None
        if vision.get("floor_status") == "NO_CANDIDATE":
            return label, "本帧未确认障碍候选", None
        return label, "当前画面未能确认障碍状态", None

    def command(self, action, request_id):
        if action in {"status", "self_check"}:
            status, _, _ = self.status()
            prefix = "眼镜状态已读取" if action == "status" else "连接与运行状态已检查"
            return f"{prefix}。{status}。"
        if action not in COMMANDS:
            raise ApiError("眼镜不支持此操作。", 400)
        if action in {"stop", "prepare", "front.confirm"}:
            service = self.runtime.service()
            if action == "stop" and service["ActiveState"] in {"inactive", "failed"}:
                self.owned_pid = None
                return "眼镜观察已停止。"
            if self.owned_pid != service["MainPID"] or self.owned_pid in {None, "0"}:
                raise ApiError("请先通过本连接启动眼镜静默观察；其他入口的任务须从原入口操作。")
        # Leave part of the phone's 45-second receipt window for transport.
        startup_deadline = time.monotonic() + 34
        result = self.actions.execute(COMMANDS[action], request_id)
        if action == "start":
            self.wait_for_observation(startup_deadline)
        if action == "stop":
            self.owned_pid = None
        return result["message"]

    def wait_for_observation(self, deadline):
        self.owned_pid = self.runtime.service()["MainPID"]
        while time.monotonic() < deadline:
            status = self.runtime.status()
            if status["phase"] == "running" and not status["stale"]:
                return
            if not status["running"]:
                self.owned_pid = None
                raise ApiError("眼镜观察初始化失败。请检查 ESP 是否供电并连上 RDK 热点，再查看设备状态。", 503)
            time.sleep(.15)
        service = self.runtime.service()
        if service["ActiveState"] not in {"inactive", "failed"}:
            if self.owned_pid != service["MainPID"] or self.owned_pid in {None, "0"}:
                self.owned_pid = None
                raise ApiError("观察初始化超时，当前任务身份已变化。请查询设备状态。", 504)
            stop_id = str(uuid4())
            try:
                self.actions.execute("stop", stop_id)
            except ApiError:
                raise ApiError("观察初始化超时，停止结果未确认。请查询设备状态或手动停止。", 504)
        self.owned_pid = None
        raise ApiError("观察初始化超时，任务已停止。请检查眼镜连接后重试。", 504)


class Endpoint:
    def __init__(self, board, token):
        self.board = board
        self.token = token
        self.connected = False
        self.navigation = Navigation()
        self.operation = None
        self.receipts = OrderedDict()

    async def handle(self, ws):
        headers = ws.request.headers.get_all("Authorization")
        if (ws.request.path not in {"/rdk", "/rdk/media"} or len(headers) != 1
                or not hmac.compare_digest(headers[0].encode(), f"Bearer {self.token}".encode())):
            await ws.close(1008, "authentication required")
            return
        if ws.request.path == "/rdk/media":
            await self.media(ws)
            return
        if self.connected:
            await ws.close(1013, "one phone at a time")
            return
        self.connected = True
        status_task = None
        try:
            hello = decode(await asyncio.wait_for(ws.recv(), 5))
            if hello.get("type") != "hello" or hello.get("client") != "yuntong-android":
                await ws.close(1008, "hello required")
                return
            await send(ws, "hello.accepted", testOnly=False, capabilities=CAPABILITIES)
            status_task = asyncio.create_task(self.report_status(ws))
            async for raw in ws:
                packet = decode(raw)
                if packet.get("type") == "device.command":
                    await self.dispatch_command(ws, packet)
                elif self.navigation.accept(packet):
                    await self.reply_navigation(ws, packet)
                else:
                    await send(ws, "rejected", sequence=packet.get("sequence"),
                               sessionId=packet.get("sessionId"), source=packet.get("source"),
                               routeVersion=packet.get("routeVersion"), reason="invalid navigation state")
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        except (ValueError, TypeError):
            await ws.close(1008, "invalid message")
        finally:
            if status_task:
                status_task.cancel()
                with suppress(asyncio.CancelledError, ConnectionClosed):
                    await status_task
            # Retain only ordering fields across reconnects, never route or location payload.
            if self.navigation.current:
                self.navigation.current = {
                    key: self.navigation.current[key]
                    for key in ("sessionId", "source", "routeVersion", "sequence", "phase")
                }
                self.navigation.route = None
                self.navigation.location_at = None
            self.connected = False

    async def media(self, ws):
        """Serve raw camera frames separately from time-sensitive control messages."""
        preview = False
        last_stamp = 0
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=.25 if preview else 30)
                except asyncio.TimeoutError:
                    raw = None
                if raw is not None:
                    packet = decode(raw)
                    kind = packet.get("type")
                    if kind == "media.subscribe":
                        preview = packet.get("enabled") is True
                    elif kind == "media.capture":
                        request_id = packet.get("requestId")
                        if not isinstance(request_id, str) or str(UUID(request_id)) != request_id:
                            raise ValueError("invalid request ID")
                        path = self.board.runtime.run / "latest_input.jpg"
                        try:
                            before_request = path.stat().st_mtime_ns
                        except OSError:
                            before_request = 0
                        frame = await asyncio.to_thread(self.read_frame, before_request, True)
                        if frame is None:
                            await send(ws, "media.unavailable", requestId=request_id)
                        else:
                            stamp, payload = frame
                            await send(ws, "frame", requestId=request_id,
                                       frameId=str(uuid4()), capturedAt=stamp // 1_000_000,
                                       jpeg=base64.b64encode(payload).decode())
                            last_stamp = stamp
                    else:
                        raise ValueError("invalid media command")
                if preview:
                    frame = await asyncio.to_thread(self.read_frame, last_stamp, False)
                    if frame is not None:
                        stamp, payload = frame
                        await send(ws, "frame", frameId=str(uuid4()),
                                   capturedAt=stamp // 1_000_000,
                                   jpeg=base64.b64encode(payload).decode())
                        last_stamp = stamp
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        except (ValueError, TypeError):
            await ws.close(1008, "invalid media command")

    def read_frame(self, after_stamp, fresh):
        path = self.board.runtime.run / "latest_input.jpg"
        deadline = time.monotonic() + (2 if fresh else 0)
        while True:
            try:
                stat = path.stat()
                if stat.st_mtime_ns > after_stamp and time.time() - stat.st_mtime <= 2:
                    payload = path.read_bytes()
                    if 4 <= len(payload) <= 700_000 and payload[:2] == b"\xff\xd8" and payload[-2:] == b"\xff\xd9":
                        return stat.st_mtime_ns, payload
            except OSError:
                pass
            if not fresh or time.monotonic() >= deadline:
                return None
            time.sleep(.08)

    async def status(self):
        try:
            return await asyncio.to_thread(self.board.status)
        except (ApiError, OSError, ValueError, TimeoutError, TimeoutExpired):
            return "暂时无法核对板端任务", "等待新的设备状态", None

    async def report_status(self, ws):
        seq = 0
        last_fall_id = None
        while True:
            status, perception, _ = await self.status()
            seq += 1
            await send(ws, "device.status", deviceSequence=seq, status=status, perception=perception)
            runtime = getattr(self.board, "runtime", None)
            try:
                fall = read_json(runtime.run / "fall_event.json") if runtime is not None else {}
            except ApiError:
                fall = {}
            event_id, stamp = fall.get("eventId"), fall.get("rdk_monotonic")
            if (fall.get("confirmed") is True and isinstance(event_id, str)
                    and 1 <= len(event_id) <= 100 and event_id != last_fall_id
                    and type(stamp) in (int, float) and 0 <= time.monotonic() - stamp <= 10):
                last_fall_id = event_id
                await send(ws, "fall.confirmed", eventId=event_id,
                           validForMs=max(1, int((10 - (time.monotonic() - stamp)) * 1000)))
            await asyncio.sleep(.5)

    async def reply_navigation(self, ws, packet):
        fields = {key: packet[key] for key in ("sessionId", "source", "routeVersion", "sequence")}
        await send(ws, "ack", **fields, stage="received")
        if packet["phase"] != "active" or packet["type"] not in {
            "navigation.snapshot", "navigation.progress", "navigation.location",
        }:
            return
        _, _, cue = await self.status()
        route = self.navigation.route
        located_at = self.navigation.location_at
        if (cue and route and located_at is not None and time.monotonic() - located_at < 5
                and route.get("status") != "recalculating"):
            text, received = cue
            remaining = int((.5 - (time.monotonic() - received)) * 1000)
            if remaining > 0:
                await send(ws, "guidance", **fields, testOnly=False, text=text, validForMs=remaining)

    async def dispatch_command(self, ws, packet):
        request_id, action = packet.get("requestId"), packet.get("action")
        if not isinstance(request_id, str) or len(request_id) != 36 or str(UUID(request_id)) != request_id:
            raise ValueError("invalid request ID")
        if not isinstance(action, str) or len(action) > 30:
            raise ValueError("invalid action")
        cached = self.receipts.get(request_id)
        if cached:
            previous_action, stage, text = cached
            if action != previous_action:
                stage, text = "failed", "请求编号已用于其他操作。"
            await send(ws, "device.command.ack", requestId=request_id, stage=stage, text=text)
            return
        if self.operation and not self.operation.done():
            await send(ws, "device.command.ack", requestId=request_id, stage="failed",
                       text="眼镜正在处理上一条操作，请稍后再试。")
            return
        await send(ws, "device.command.ack", requestId=request_id, stage="received", text="眼镜已接收，等待执行结果")
        self.operation = asyncio.create_task(self.run_command(ws, action, request_id))

    async def run_command(self, ws, action, request_id):
        try:
            text = await asyncio.to_thread(self.board.command, action, request_id)
            stage = "executed"
        except ApiError as exc:
            stage, text = "failed", str(exc)
        except Exception:
            stage, text = "failed", "眼镜操作结果未确认，请刷新状态。"
        self.receipts[request_id] = (action, stage, text)
        if len(self.receipts) > 128:
            self.receipts.popitem(last=False)
        with suppress(ConnectionClosed):
            await send(ws, "device.command.ack", requestId=request_id, stage=stage, text=text)


async def main(args):
    token = args.token_file.read_text(encoding="utf-8").strip()
    if not 16 <= len(token) <= 128 or not all("!" <= c <= "~" for c in token):
        raise ValueError("Use a 16–128 character ASCII pairing token")
    endpoint = Endpoint(Board(Runtime(args.runtime, archives=args.archives)), token)
    tls = None
    if args.cert:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(args.cert, args.key)
    async with serve(endpoint.handle, args.host, args.port, ssl=tls,
                     max_size=512_000, max_queue=8, compression=None):
        print(f"Yuntong RDK listening on {args.host}:{args.port}/rdk", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--archives", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--cert", type=Path)
    parser.add_argument("--key", type=Path)
    options = parser.parse_args()
    if bool(options.cert) != bool(options.key):
        parser.error("--cert and --key must be given together")
    try:
        asyncio.run(main(options))
    except KeyboardInterrupt:
        pass
