"""Candidate relay: poll SSH receive readiness without Channel.fileno()."""
import select
import socket
import socketserver
import threading
from x5_camera_link import X5CameraLink as BaseCameraLink


class X5CameraLink(BaseCameraLink):
    """Reuse MAC discovery and bounded teardown, keeping frozen code intact.

    Paramiko's Channel.fileno creates a notification socket pair on Windows.
    Avoid that extra lifecycle entirely; select only the owned local socket,
    and poll the SSH receive buffer. The handler still owns channel.close().
    """
    def _listen(self, remote_port):
        link=self
        if not hasattr(self,'relay_errors'):
            self.relay_errors=[]
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                if not link.slots.acquire(blocking=False):return
                channel=None;pair=None
                try:
                    with link.lock:
                        if link.stop.is_set():return
                        link.handlers.add(threading.current_thread())
                    channel=link.transport.open_channel('direct-tcpip',(link.host,remote_port),self.client_address,timeout=3)
                    pair=(self.request,channel)
                    with link.lock:
                        if link.stop.is_set():return
                        link.connections.add(pair)
                    self.request.settimeout(2);channel.settimeout(2)
                    while not link.stop.is_set():
                        ready,_,_=select.select([self.request],[],[],.01)
                        if ready:
                            data=self.request.recv(65536)
                            if not data:return
                            channel.sendall(data)
                        if channel.recv_ready():
                            data=channel.recv(65536)
                            if not data:return
                            self.request.sendall(data)
                        elif channel.closed or channel.eof_received:return
                except Exception as exc:
                    if not link.stop.is_set():
                        with link.lock:
                            link.relay_errors.append(dict(port=remote_port,error=repr(exc)))
                            del link.relay_errors[:-32]
                finally:
                    if channel:
                        try:channel.close()
                        except (OSError,EOFError):pass
                    with link.lock:
                        if pair:link.connections.discard(pair)
                        link.handlers.discard(threading.current_thread())
                    link.slots.release()
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads=True
            allow_reuse_address=False
        server=Server(('127.0.0.1',0),Handler);self.servers.append(server)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.1},daemon=True)
        self.threads.append(thread);thread.start()
        return 'http://127.0.0.1:'+str(server.server_address[1])
