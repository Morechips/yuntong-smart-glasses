"""Loopback-only SSH links to the MAC-verified ESP on X5's existing WLAN."""
import ipaddress,json,select,shlex,socket,socketserver,threading,time

from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'ground_route_screen_20261031'))
from device_settings import ESP_MAC, require_esp_identity


class X5CameraLink:
    def __init__(self,backend):
        require_esp_identity()
        code="import subprocess;print(subprocess.check_output(['ip','-j','neigh','show','dev','wlan0']).decode())"
        reply=backend.run(backend.transport,'python3 -c '+shlex.quote(code),timeout=5)
        if reply['code']!=0:raise RuntimeError('无法读取RDK热点内ESP地址')
        hosts={row['dst'] for row in json.loads(reply['stdout']) if row.get('lladdr','').lower()==ESP_MAC}
        hosts={host for host in hosts if ipaddress.ip_address(host).version==4 and ipaddress.ip_address(host).is_private}
        if len(hosts)!=1:raise RuntimeError('RDK热点未发现唯一ESP地址，请检查ESP上电和连接')
        self.host=hosts.pop();self.transport=backend.transport;self.stop=threading.Event()
        self.lock=threading.Lock();self.connections=set();self.handlers=set();self.servers=[];self.threads=[]
        self.slots=threading.BoundedSemaphore(6)
        try:
            self.camera_url=self._listen(80)
            self.peripheral_url=self._listen(8081)
        except Exception:self.close();raise
    def _listen(self,remote_port):
        link=self
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
                        ready,_,_=select.select(pair,[],[],.2)
                        for source in ready:
                            data=source.recv(65536)
                            if not data:return
                            (channel if source is self.request else self.request).sendall(data)
                except Exception:pass # Caller sees EOF/timeout and clears cues.
                finally:
                    # The handler owns its channel. Closing it simultaneously
                    # from another thread can race Paramiko's Windows pipe.
                    if channel:
                        try:channel.close()
                        except (OSError,EOFError):pass
                    if pair:
                        with link.lock:link.connections.discard(pair)
                    with link.lock:link.handlers.discard(threading.current_thread())
                    link.slots.release()
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads=True
            allow_reuse_address=False
        server=Server(('127.0.0.1',0),Handler);self.servers.append(server)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.1},daemon=True)
        self.threads.append(thread);thread.start()
        return 'http://127.0.0.1:'+str(server.server_address[1])
    def close(self):
        self.stop.set()
        with self.lock:connections=list(self.connections)
        for local,_ in connections:
            try:local.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        for server in self.servers:server.shutdown();server.server_close()
        for thread in self.threads:thread.join(2)
        deadline=time.monotonic()+5
        with self.lock:handlers=list(self.handlers)
        for thread in handlers:thread.join(max(0,deadline-time.monotonic()))
        if any(thread.is_alive() for thread in handlers):raise RuntimeError('RDK图传转发线程尚未退出')
