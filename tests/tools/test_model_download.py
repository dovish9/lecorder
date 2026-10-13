"""Real local HTTP downloads; no network provider or large model required."""
import hashlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from scripts.model_assets import install


class ModelDownloadTests(unittest.TestCase):
    def test_truncated_response_then_successful_retry(self):
        body=b'small model fixture'
        attempts=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                attempts.append(self.path)
                self.send_response(200)
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body[:3] if len(attempts)==1 else body)
                self.close_connection=True
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                target=Path(folder)/'model.bin'
                item=dict(url=f'http://127.0.0.1:{server.server_port}/model',size=len(body),sha256=hashlib.sha256(body).hexdigest())
                with self.assertRaises(ValueError):install(target,item)
                self.assertEqual(list(Path(folder).iterdir()),[])
                install(target,item)
                install(target,item)
                self.assertEqual(target.read_bytes(),body)
                self.assertEqual(len(attempts),2)
        finally:
            server.shutdown();server.server_close();thread.join()
