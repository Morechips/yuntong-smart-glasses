"""Preview must not create data, reconnect, or stop when recording completes."""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from app import App
from live_stream import LiveStream
from recorder import Recorder
from test_recorder import Server
from verify_dataset import verify

def wait_for(predicate,seconds=4,root=None):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if root:root.update()
        if predicate():return True
        time.sleep(.01)
    return False

class Tests(unittest.TestCase):
    def test_preview_has_no_output_and_recording_reuses_connection(self):
        with tempfile.TemporaryDirectory() as tmp,Server() as server:
            live=LiveStream(server.url);live.start()
            self.assertTrue(wait_for(lambda:live.snapshot()['received']>=3))
            self.assertEqual(list(Path(tmp).iterdir()),[])
            rec=Recorder(server.url,tmp,'site','batch','scene',seconds=1,stream=live)
            folder=rec.start();self.assertTrue(rec.done.wait(3));self.assertFalse(rec.error)
            count=live.snapshot()['received'];self.assertTrue(wait_for(lambda:live.snapshot()['received']>count+2))
            self.assertFalse(live.done.is_set());self.assertEqual(verify(folder)['status'],'passed')
            report=json.loads((folder/'report.json').read_text('utf-8'));self.assertTrue(report['shared_preview_stream'])
            self.assertEqual(server.requests.count('/stream'),1)
            self.assertNotIn('/stats',server.requests)
            live.stop();self.assertTrue(live.done.wait(3))

    def test_gui_preview_blank_site_then_record_and_preview_again(self):
        with tempfile.TemporaryDirectory() as tmp,Server() as server:
            root=tk.Tk();root.withdraw();app=App(root)
            try:
                app.output.set(tmp);app.url.set(server.url);app.connect_preview()
                self.assertTrue(wait_for(lambda:app.photo is not None,root=root))
                self.assertEqual(app.site.get(),'');self.assertIsNone(app.session);self.assertEqual(list(Path(tmp).iterdir()),[])
                source=app.live
                app.site.set('site');app.batch.set('batch');app.seconds.set('1');app.start()
                self.assertIs(app.live,source);self.assertTrue(wait_for(lambda:app.session.done.is_set(),root=root))
                self.assertTrue(wait_for(lambda:app.session_handled,root=root));self.assertFalse(app.session.error)
                count=source.snapshot()['received'];self.assertTrue(wait_for(lambda:source.snapshot()['received']>count+2,root=root))
                self.assertEqual(str(app.start_button['state']),'normal')
            finally:
                app.close();self.assertTrue(wait_for(lambda:source.done.is_set() if 'source' in locals() else True,root=root))
                try:root.update()
                except tk.TclError:pass

    def test_disconnect_during_shared_recording_persists_failure(self):
        with tempfile.TemporaryDirectory() as tmp,Server() as server:
            live=LiveStream(server.url);live.start();self.assertTrue(wait_for(lambda:live.snapshot()['received']>=2))
            rec=Recorder(server.url,tmp,'site','batch','scene',seconds=5,stream=live);folder=rec.start()
            time.sleep(.1);live.stop();self.assertTrue(rec.done.wait(3));self.assertTrue(rec.error)
            report=json.loads((folder/'report.json').read_text('utf-8'));self.assertEqual(report['status'],'failed')

if __name__=='__main__':unittest.main(verbosity=2)
