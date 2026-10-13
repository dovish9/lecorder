"""Real HTTP + child process checks; synthetic data only, no model or API key needed."""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from web.backend.ai_transport import request_json, ProviderError
from web.backend.processes import CancellationToken, TranscriptionCancelled


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.server.requests.append((self.path, dict(self.headers), body))
        self.server.received.set()
        if self.path == '/slow':
            self.server.release.wait(5)
        status = {'/unauthorized':401, '/limited':429, '/redirect':302}.get(self.path, 200)
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        if status == 302: self.send_header('Location','/echo')
        self.end_headers()
        output = b'sensitive error text' if self.path in ['/bad','/unauthorized'] else json.dumps({'ok':True}).encode()
        try: self.wfile.write(output)
        except (BrokenPipeError, ConnectionResetError): pass


class HttpTransportTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.server.requests = []
        self.server.received = threading.Event()
        self.server.release = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.release.set()
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def test_json_and_multipart_keep_unicode_and_binary_content(self):
        self.assertTrue(request_json(self.url+'/echo', {'text':'강의'}, headers={'Authorization':'Bearer synthetic'})['ok'])
        _, headers, body = self.server.requests[-1]
        self.assertEqual(json.loads(body), {'text':'강의'})
        self.assertEqual(headers['Authorization'],'Bearer synthetic')
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp)/'음성 파일.wav'; audio.write_bytes(b'RIFFsynthetic')
            request_json(self.url+'/echo', multipart={'file':str(audio),'language':'ko'})
        self.assertIn(b'RIFFsynthetic', self.server.requests[-1][2])
        self.assertIn(b'name="language"', self.server.requests[-1][2])

    def test_auth_json_and_redirect_errors_are_sanitized(self):
        for endpoint, message in [('/unauthorized','인증'),('/bad','JSON'),('/redirect','302')]:
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ProviderError,message) as error:
                    request_json(self.url+endpoint, headers={'Authorization':'Bearer never-echo-me'})
                self.assertNotIn('sensitive', str(error.exception))
                self.assertNotIn('never-echo-me', str(error.exception))
        self.assertEqual(len(self.server.requests),3)

    def test_rate_limit_retries_only_once(self):
        with self.assertRaisesRegex(ProviderError,'429'):
            request_json(self.url+'/limited')
        self.assertEqual(len(self.server.requests),2)

    def test_timeout_reaps_worker(self):
        token=CancellationToken()
        with self.assertRaisesRegex(ProviderError,'초과'):
            request_json(self.url+'/slow', timeout=.1, cancellation=token)
        self.assertEqual(token._processes,set())

    def test_inflight_cancel_is_prompt_and_reaps_worker(self):
        token=CancellationToken(); errors=[]
        def invoke():
            try: request_json(self.url+'/slow', cancellation=token)
            except Exception as error: errors.append(error)
        thread=threading.Thread(target=invoke);thread.start()
        self.assertTrue(self.server.received.wait(5))
        started=time.monotonic();token.cancel();thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic()-started,3)
        self.assertIsInstance(errors[0],TranscriptionCancelled)
        self.assertEqual(token._processes,set())
