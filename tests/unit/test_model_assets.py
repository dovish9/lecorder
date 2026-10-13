import hashlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.model_assets import install, valid, probe_executable


class ModelAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / 'model.bin'
        self.body = b'validated model'
        self.item = dict(url='https://example.test/model', size=len(self.body),
                         sha256=hashlib.sha256(self.body).hexdigest())

    def test_download_then_reuse_without_network(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(self.body)) as network:
            install(self.target, self.item)
            install(self.target, self.item)
            network.assert_called_once()
        self.assertTrue(valid(self.target, self.item))

    def test_existing_invalid_file_is_preserved(self):
        self.target.write_bytes(b'user model')
        with patch('urllib.request.urlopen') as network, self.assertRaises(ValueError):
            install(self.target, self.item)
        network.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'user model')

    def test_bad_download_does_not_publish_and_retry_works(self):
        for body in (b'short', b'x' * 100):
            with self.subTest(body=body), patch('urllib.request.urlopen', return_value=io.BytesIO(body)), self.assertRaises(ValueError):
                install(self.target, self.item)
            self.assertEqual(list(self.target.parent.iterdir()), [])
        with patch('urllib.request.urlopen', return_value=io.BytesIO(self.body)):
            install(self.target, self.item)
        self.assertTrue(valid(self.target, self.item))

    def test_interrupted_download_preserves_pointer_and_cleans_temp(self):
        pointer = b'version https://git-lfs.github.com/spec/v1\noid sha256:test\n'
        self.target.write_bytes(pointer)
        class Interrupted(io.BytesIO):
            def read(self, size=-1):
                raise OSError('connection interrupted')
        with patch('urllib.request.urlopen', return_value=Interrupted()), self.assertRaises(OSError):
            install(self.target, self.item)
        self.assertEqual(self.target.read_bytes(), pointer)
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])
        with patch('urllib.request.urlopen', return_value=io.BytesIO(self.body)):
            install(self.target, self.item)
        self.assertTrue(valid(self.target, self.item))


class ExecutableProbeTests(unittest.TestCase):
    def test_probe_success_uses_help_with_timeout(self):
        with patch('scripts.model_assets.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'help')) as run:
            probe_executable(Path('/tmp/native helper'))
        self.assertEqual(run.call_args.args[0], ['/tmp/native helper', '--help'])
        self.assertEqual(run.call_args.kwargs['timeout'], 20)

    def test_loader_failure_is_reported(self):
        with patch('scripts.model_assets.subprocess.run', return_value=subprocess.CompletedProcess([], -6, 'Library not loaded')), self.assertRaisesRegex(ValueError, 'Library not loaded'):
            probe_executable(Path('/tmp/broken'))

    def test_timeout_and_missing_executable_are_failures(self):
        for error in (subprocess.TimeoutExpired('helper', 20), FileNotFoundError('missing')):
            with self.subTest(error=error), patch('scripts.model_assets.subprocess.run', side_effect=error), self.assertRaises(ValueError):
                probe_executable(Path('/tmp/helper'))
