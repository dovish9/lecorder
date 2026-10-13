import math
import os
import subprocess
import tempfile
import threading
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from web.backend.media import request
from web.backend.note_extract import document_request, prepare_pages
from web.backend.transcription import CancellationToken, TranscriptionCancelled


class PortableMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wav = self.root / 'voice.wav'
        with wave.open(str(self.wav), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            samples = array('h', (round(10000 * math.sin(i * .2)) if i < 16000 or i >= 32000 else 0 for i in range(48000)))
            import sys
            if sys.byteorder != 'little': samples.byteswap()
            audio.writeframes(samples.tobytes())

    def test_conversion_and_playback_without_executables(self):
        with patch.dict(os.environ, {'PATH': ''}):
            for extension in ('mp4', 'webm'):
                compressed = self.root / ('encoded.' + extension)
                request('transcode', source=self.wav, destination=compressed, playback=True)
                info = request('probe', source=compressed)
                self.assertAlmostEqual(info['duration'], 3, delta=.05)
                self.assertFalse(info['short_frames'])
                decoded = self.root / 'decoded.wav'
                request('transcode', source=compressed, destination=decoded)
                with wave.open(str(decoded)) as audio:
                    self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 16000))
                    self.assertEqual(audio.getnframes(), 48000)

    def test_short_opus_frames_are_normalized(self):
        import av
        from web.backend.output_files import prepare_webm_playback
        target = self.root / "short.webm"
        with av.open(str(self.wav)) as source, av.open(str(target), "w") as output:
            stream = output.add_stream("libopus", rate=48000)
            stream.layout = "mono"
            stream.options = {"frame_duration": "2.5"}
            resampler = av.AudioResampler(format="flt", layout="mono", rate=48000)
            for frame in source.decode(audio=0):
                for converted in resampler.resample(frame):
                    for packet in stream.encode(converted): output.mux(packet)
            for converted in resampler.resample(None):
                for packet in stream.encode(converted): output.mux(packet)
            for packet in stream.encode(None): output.mux(packet)
        self.assertTrue(request("probe", source=target)["short_frames"])
        prepared, duration = prepare_webm_playback(target)
        self.assertTrue(prepared)
        self.assertAlmostEqual(duration, 3, delta=.05)
        self.assertFalse(request("probe", source=target)["short_frames"])
        self.assertEqual(prepare_webm_playback(target), (False, duration))

    def test_clip_and_silence_are_sample_accurate(self):
        clip = self.root / 'clip.wav'
        request('clip', source=self.wav, destination=clip, start=.5, duration=1.5)
        with wave.open(str(clip)) as a, wave.open(str(self.wav)) as b:
            b.setpos(8000)
            self.assertEqual(a.getnframes(), 24000)
            self.assertEqual(a.readframes(24000), b.readframes(24000))
        points = request('silences', source=self.wav)['points']
        self.assertEqual(len(points), 1)
        self.assertAlmostEqual(points[0], 1.5, delta=.001)

    def test_corrupt_audio_fails_and_cancel_stops_worker(self):
        invalid = self.root / 'bad.wav'; invalid.write_bytes(b'not audio')
        with self.assertRaises(subprocess.CalledProcessError):
            request('transcode', source=invalid, destination=self.root/'bad-out.wav')
        token = CancellationToken()
        timer = threading.Timer(.01, token.cancel)
        timer.start()
        try:
            with self.assertRaises(TranscriptionCancelled):
                request('transcode', token, source=self.wav, destination=self.root/'cancel.wav')
        finally:
            timer.join()
        self.assertFalse(token._processes)

    def test_pdf_render_and_image_orientation_without_os_tools(self):
        from PIL import Image
        source = self.root/'sample.pdf'
        Image.new('RGB', (300, 200), 'white').save(source, 'PDF')
        token = CancellationToken()
        with patch.dict(os.environ, {'PATH': ''}):
            self.assertEqual(prepare_pages([source], self.root, token), [(source, 1)])
            rendered = self.root/'pdf.png'
            document_request(token, operation='pdf', source=str(source), number=1, destination=str(rendered))
            with Image.open(rendered) as result:
                self.assertEqual(max(result.size), 2200)
            for extension in ('jpg', 'heic'):
                from pillow_heif import register_heif_opener
                register_heif_opener()
                original = self.root / ('oriented.'+extension)
                exif = Image.Exif(); exif[274] = 6
                Image.new('RGB', (120,80), 'red').save(original, exif=exif)
                from PIL import ImageOps
                with Image.open(original) as saved:
                    expected_size = ImageOps.exif_transpose(saved).size
                if extension == 'jpg': self.assertEqual(expected_size, (80,120))
                target = self.root/'image.png'
                document_request(token, operation='image', source=str(original), destination=str(target))
                with Image.open(target) as result:
                    self.assertEqual(result.size, expected_size)


if __name__ == '__main__': unittest.main()
