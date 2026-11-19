import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from server import ApiError, Board, Endpoint, Navigation, SCHEMA


def packet(kind, sequence=1, phase="active", **fields):
    return dict(schema=SCHEMA, type=kind, phase=phase, source="amap",
                sessionId="route-a", routeVersion=1, sequence=sequence) | fields


class NavigationTest(unittest.TestCase):
    def test_simulation_old_route_duplicate_and_ended_session(self):
        gate = Navigation()
        self.assertFalse(gate.accept(packet("session.start", source="simulation")))
        self.assertTrue(gate.accept(packet("session.start")))
        self.assertFalse(gate.accept(packet("navigation.progress")))
        self.assertTrue(gate.accept(packet("route.replace", 2, routeVersion=2)))
        self.assertFalse(gate.accept(packet("navigation.progress", 3)))
        self.assertTrue(gate.accept(packet("user.action", 3, routeVersion=2,
                                           action="destination.nearby")))
        self.assertTrue(gate.accept(packet("session.paused", 4, "paused", routeVersion=2)))
        self.assertFalse(gate.accept(packet("navigation.progress", 5, routeVersion=2)))
        self.assertTrue(gate.accept(packet("session.ended", 5, "ended", routeVersion=2)))
        self.assertFalse(gate.accept(packet("navigation.snapshot", 6, routeVersion=2)))


class StatusTest(unittest.TestCase):
    def test_start_requires_fresh_runtime_and_reports_early_exit(self):
        class Runtime:
            def service(self):
                return {"MainPID": "42"}

            def status(self):
                return next(self.phases)

        runtime = Runtime()
        board = Board(runtime)
        runtime.phases = iter([
            {"phase": "starting", "running": True, "stale": True},
            {"phase": "running", "running": True, "stale": True},
            {"phase": "running", "running": True, "stale": False},
        ])
        board.wait_for_observation(time.monotonic() + 2)
        self.assertEqual(board.owned_pid, "42")
        runtime.phases = iter([{"phase": "failed", "running": False, "stale": False}])
        with self.assertRaisesRegex(ApiError, "初始化失败"):
            board.wait_for_observation(time.monotonic() + 2)
        self.assertIsNone(board.owned_pid)

    def test_stale_pose_and_result_never_create_prompt(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)

            class Runtime:
                run = path

                def status(self):
                    return {"phase": "running", "stale": False}

            board = Board(Runtime())
            status_file = path / "wear_status.json"
            for state, age, expected in [
                ("ready", .1, True), ("wait_front", .1, False), ("ready", .6, False),
            ]:
                now = time.monotonic()
                status_file.write_text(json.dumps(dict(mode="shadow", active=True,
                    state=state, rdk_monotonic=now, vision=dict(received=now-age,
                    direction="CENTER", floor_status="OBSTACLE"))), encoding="utf-8")
                self.assertEqual(bool(board.status()[2]), expected)


class RoundTripTest(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_fall_is_forwarded_once(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            event_id = str(uuid4())
            (path / "fall_event.json").write_text(json.dumps(dict(
                eventId=event_id, confirmed=True, rdk_monotonic=time.monotonic())))

            class Fixture:
                runtime = type("Runtime", (), {"run": path})()

                def status(self):
                    return "ready", "candidate", None

            endpoint = Endpoint(Fixture(), "fixture-only-token")
            async with serve(endpoint.handle, "127.0.0.1", 0) as server:
                url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/rdk"
                headers = {"Authorization": "Bearer fixture-only-token"}
                async with connect(url, additional_headers=headers, proxy=None) as ws:
                    await ws.send(json.dumps(dict(type="hello", schema=SCHEMA, client="yuntong-android")))
                    seen = []
                    for _ in range(4):
                        packet = json.loads(await asyncio.wait_for(ws.recv(), 2))
                        if packet["type"] == "fall.confirmed":
                            seen.append(packet)
                            break
                    self.assertEqual(len(seen), 1)
                    self.assertEqual(seen[0]["eventId"], event_id)
                    for _ in range(3):
                        packet = json.loads(await asyncio.wait_for(ws.recv(), 2))
                        self.assertNotEqual(packet["type"], "fall.confirmed")

    async def test_media_requires_auth_and_capture_waits_for_new_frame(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)

            class Fixture:
                runtime = type("Runtime", (), {"run": path})()

            endpoint = Endpoint(Fixture(), "fixture-only-token")
            async with serve(endpoint.handle, "127.0.0.1", 0) as server:
                url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/rdk/media"
                async with connect(url, proxy=None) as unauthorized:
                    with self.assertRaises(ConnectionClosed):
                        await unauthorized.recv()
                headers = {"Authorization": "Bearer fixture-only-token"}
                image = path / "latest_input.jpg"
                image.write_bytes(b"\xff\xd8old\xff\xd9")
                async with connect(url, additional_headers=headers, proxy=None) as ws:
                    await ws.send(json.dumps(dict(schema=SCHEMA, type="media.subscribe", enabled=True)))
                    preview = json.loads(await asyncio.wait_for(ws.recv(), 2))
                    self.assertEqual(preview["type"], "frame")
                    self.assertNotIn("requestId", preview)
                    await ws.send(json.dumps(dict(schema=SCHEMA, type="media.subscribe", enabled=False)))
                    request_id = str(uuid4())
                    await ws.send(json.dumps(dict(schema=SCHEMA, type="media.capture", requestId=request_id)))
                    await asyncio.sleep(.15)
                    image.write_bytes(b"\xff\xd8new\xff\xd9")
                    captured = json.loads(await asyncio.wait_for(ws.recv(), 3))
                    self.assertEqual(captured["requestId"], request_id)
                    self.assertNotEqual(captured["jpeg"], preview["jpeg"])
                    unfulfilled_id = str(uuid4())
                    await ws.send(json.dumps(dict(schema=SCHEMA, type="media.capture", requestId=unfulfilled_id)))
                    unavailable = json.loads(await asyncio.wait_for(ws.recv(), 3))
                    self.assertEqual(unavailable["type"], "media.unavailable")
                    self.assertEqual(unavailable["requestId"], unfulfilled_id)

    async def test_authenticated_status_command_guidance_and_reconnect(self):
        class BoardFixture:
            calls = 0

            def status(self):
                now = time.monotonic()
                return "ready", "candidate", ("test cue", now - .1)

            def command(self, action, request_id):
                self.calls += 1
                return "confirmed"

        board = BoardFixture()
        token = "fixture-only-token"
        endpoint = Endpoint(board, token)
        request_id = str(uuid4())

        async def read(ws, kind):
            while True:
                received = json.loads(await asyncio.wait_for(ws.recv(), 2))
                if received["type"] == kind:
                    return received

        async def hello(ws):
            await ws.send(json.dumps(dict(type="hello", schema=SCHEMA, client="yuntong-android")))
            self.assertIs((await read(ws, "hello.accepted"))["testOnly"], False)

        async with serve(endpoint.handle, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/rdk"
            async with connect(url, proxy=None) as unauthorized:
                with self.assertRaises(ConnectionClosed):
                    await unauthorized.recv()
            headers = {"Authorization": f"Bearer {token}"}
            async with connect(url, additional_headers=headers, proxy=None) as ws:
                await hello(ws)
                self.assertEqual((await read(ws, "device.status"))["status"], "ready")
                command = dict(schema=SCHEMA, type="device.command", action="prepare", requestId=request_id)
                await ws.send(json.dumps(command))
                self.assertEqual((await read(ws, "device.command.ack"))["stage"], "received")
                self.assertEqual((await read(ws, "device.command.ack"))["stage"], "executed")
                await ws.send(json.dumps(packet("session.start", route={"length": 350})))
                await read(ws, "ack")
                await ws.send(json.dumps(packet("navigation.location", 2,
                    location={"latitude": 23.0, "longitude": 113.0})))
                cue = await read(ws, "guidance")
                self.assertEqual(cue["sequence"], 2)
                self.assertIn(cue["validForMs"], range(1, 501))
                await ws.send(json.dumps(packet("session.ended", 3, "ended")))
                await read(ws, "ack")
            async with connect(url, additional_headers=headers, proxy=None) as ws:
                await hello(ws)
                await ws.send(json.dumps(command))
                self.assertEqual((await read(ws, "device.command.ack"))["stage"], "executed")
                self.assertEqual(board.calls, 1)
                await ws.send(json.dumps(packet("navigation.snapshot", 4)))
                rejected = await read(ws, "rejected")
                self.assertEqual(rejected["sequence"], 4)
                self.assertEqual(rejected["sessionId"], "route-a")
                self.assertEqual(rejected["source"], "amap")
                self.assertEqual(rejected["routeVersion"], 1)
