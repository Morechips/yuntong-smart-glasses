"""Actual local sockets with a channel wrapper forbidding fileno access."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import select
import socket
import threading
import unittest
import urllib.request
from types import SimpleNamespace
import paths
from camera_link import X5CameraLink


class RelayTests(unittest.TestCase):
    def test_http_and_stop_do_not_allocate_channel_select_pipe(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                body=b'{"ok":true}';self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();channels=[]
        class Channel:
            def __init__(self):
                self.sock=socket.create_connection(server.server_address,timeout=2);self.closed=False;self.eof_received=False
            def fileno(self):raise AssertionError('Relay must not use Channel.fileno')
            def settimeout(self,value):self.sock.settimeout(value)
            def sendall(self,data):self.sock.sendall(data)
            def recv_ready(self):return not self.closed and bool(select.select([self.sock],[],[],0)[0])
            def recv(self,n):
                data=self.sock.recv(n)
                if not data:self.eof_received=True
                return data
            def close(self):
                if not self.closed:self.closed=True;self.sock.close()
        class Transport:
            def open_channel(self,*args,**kwargs):
                channel=Channel();channels.append(channel);return channel
        def run(*args,**kwargs):return dict(code=0,stdout=json.dumps([dict(dst='10.5.5.52',lladdr='90:70:69:fe:56:0c')]))
        link=X5CameraLink(SimpleNamespace(transport=Transport(),run=run))
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(15):
                with opener.open(link.peripheral_url+'/state',timeout=3) as response:self.assertEqual(json.load(response),{'ok':True})
            # Exercise close while a handler is still waiting for client input.
            address=link.servers[0].server_address;client=socket.create_connection(address,timeout=2)
            try:link.close()
            finally:client.close()
            self.assertTrue(all(c.closed for c in channels));self.assertFalse(link.handlers)
            self.assertFalse(link.relay_errors)
        finally:
            if not link.stop.is_set():link.close()
            server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
