"""Host contracts run on every OS without invoking another OS's tools."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web.backend.atomic import write_text
from web.backend.config import _read_env
from web.backend.files import safe_name
from web.backend.platforms import MacHost, WindowsHost, PosixHost, select_host
from web.backend.ocr import NativeOCR
from web.backend.processes import CancellationToken
from web.backend.ai_settings import Credentials


class PlatformTests(unittest.TestCase):
    def test_selection_and_capabilities_are_explicit(self):
        for system, host, name in [('darwin', MacHost, 'macos'), ('win32', WindowsHost, 'windows'), ('linux', PosixHost, 'linux')]:
            with self.subTest(system=system):
                platform = select_host(system)
                self.assertIsInstance(platform, host)
                self.assertEqual(platform.capabilities()['platform'], name)
        self.assertFalse(WindowsHost().capabilities()['vision_ocr'])

    def test_windows_executable_release_and_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'한글 폴더/bin'
            (folder/'Release').mkdir(parents=True)
            target = folder/'Release/whisper-server.exe'
            target.touch()
            self.assertEqual(WindowsHost().executable(folder, 'whisper-server'), target)
            self.assertEqual(WindowsHost().executable(folder, 'whisper-server.exe'), target)
            direct = folder/'whisper-server.exe'; direct.touch()
            self.assertEqual(WindowsHost().executable(folder, 'whisper-server'), direct)

    def test_reserved_filenames_and_trailing_dots(self):
        for name in ['CON', 'con.txt', 'PRN', 'COM1', 'LPT9']:
            with self.subTest(name=name):
                self.assertTrue(safe_name(name).startswith('_'))
        self.assertEqual(safe_name('강의 기록. '), '강의 기록')
        self.assertEqual(safe_name('Convolution'), 'Convolution')

    def test_environment_preserves_windows_backslashes_and_unicode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'.env'
            path.write_text('OUTPUT_DIR="C:\\Users\\이름\\My Notes"\n', encoding='utf-8')
            self.assertEqual(_read_env(path)['OUTPUT_DIR'], 'C:\\Users\\이름\\My Notes')

    def test_atomic_replace_is_repeatable_without_leaking_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'한글.json'
            for text in ['첫 번째', '두 번째']:
                write_text(path, text)
                self.assertEqual(path.read_text(encoding='utf-8'), text)
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_reveal_uses_argv_not_shell(self):
        path = Path('한글 폴더')/'a & b.wav'
        with patch('web.backend.platforms.subprocess.Popen') as launch:
            MacHost().reveal(path)
            self.assertEqual(launch.call_args.args[0], ['/usr/bin/open', '-R', str(path)])
            WindowsHost().reveal(path)
            self.assertEqual(launch.call_args.args[0], ['explorer.exe', '/select,', str(path)])
            self.assertNotIn('shell', launch.call_args.kwargs)

    def test_unsupported_secret_store_and_vision_fail_closed(self):
        with self.assertRaises(RuntimeError):
            Credentials(PosixHost()).set({'provider':'openai','base_url':'https://example.test'}, 'secret')
        with patch('web.backend.ocr.run') as run:
            with self.assertRaisesRegex(ValueError, 'Vision'):
                NativeOCR(WindowsHost(), 'vision').recognize(Path('image.png'), CancellationToken())
            run.assert_not_called()

    def test_native_library_environment_preserves_existing_search_path(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PATH':'existing'}):
            env = WindowsHost().library_environment([Path(tmp)])
            self.assertEqual(env['PATH'], tmp + ';existing')
