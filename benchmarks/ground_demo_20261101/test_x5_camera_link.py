"""SSH channel routing tested with local HTTP endpoints only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
import json,socket,threading,unittest,urllib.request
from x5_camera_link import X5CameraLink
from unittest.mock import patch
import x5_camera_link
ESP_MAC='00:11:22:33:44:55'


def backend(rows,transport=None):
    return SimpleNamespace(transport=transport,run=lambda *a,**k:dict(code=0,stdout=json.dumps(rows)))


class CameraLinkTests(unittest.TestCase):
    def setUp(self):
        mac=patch.object(x5_camera_link,'ESP_MAC',ESP_MAC);mac.start();self.addCleanup(mac.stop)
        identity=patch.object(x5_camera_link,'require_esp_identity',lambda:None);identity.start();self.addCleanup(identity.stop)
    def test_missing_mac_never_opens_a_link(self):
        with self.assertRaises(RuntimeError):X5CameraLink(backend([dict(dst='10.5.5.99',lladdr='00:00:00:00:00:01')]))
    def test_ambiguous_addresses_never_open_a_link(self):
        with self.assertRaises(RuntimeError):X5CameraLink(backend([dict(dst=ip,lladdr=ESP_MAC) for ip in ['10.5.5.51','10.5.5.52']]))
    def test_both_ports_are_loopback_only_and_cleanup_is_bounded(self):
        requests=[];channels=[];hold=threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                requests.append(self.path);body=b'{"ok":true}'
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
                if self.path=='/hold':hold.wait(3);return
                self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        class Transport:
            def open_channel(self,kind,destination,source,timeout):
                channels.append((kind,destination,source));return socket.create_connection(server.server_address,timeout)
        link=None
        try:
            link=X5CameraLink(backend([dict(dst='10.5.5.52',lladdr=ESP_MAC)],Transport()))
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for url in [link.camera_url+'/stream',link.peripheral_url+'/state']:
                with opener.open(url,timeout=2) as response:self.assertTrue(json.load(response)['ok'])
            self.assertEqual(requests,['/stream','/state'])
            self.assertEqual([c[1] for c in channels],[('10.5.5.52',80),('10.5.5.52',8081)])
            self.assertTrue(all(s.server_address[0]=='127.0.0.1' for s in link.servers))
            pending=opener.open(link.camera_url+'/hold',timeout=2)
            self.assertTrue(link.handlers)
            link.close();self.assertTrue(all(not t.is_alive() for t in link.threads))
            self.assertFalse(link.handlers);pending.close()
        finally:
            hold.set()
            if link:link.close()
            server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
