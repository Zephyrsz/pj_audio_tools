import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

from voice_tools import AudioError, extract_clip, parse_time


class TimeTests(unittest.TestCase):
    def test_seconds_and_clock_notation(self):
        for value, expected in [(0, 0), (100.125, 100.125), (1e-6, 0.000001), ("100", 100),
                                ("01:40", 100), ("1:02:03.250", 3723.25),
                                (" 00:00.125 ", 0.125), ("102:06", 6126)]:
            with self.subTest(value=value):
                self.assertEqual(parse_time(value), expected)

    def test_invalid_times_rejected(self):
        for value in ["", "abc", "1:60", "1:60:00", "-1", -1,
                      "NaN", float("inf"), float("nan"), "1::2", "1:2:3:4",
                      "1.2:03", True, None]:
            with self.subTest(value=value):
                with self.assertRaises(AudioError):
                    parse_time(value)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg")
class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "中文 音频.wav"
        # Every sample varies with its position, so a wrong seek cannot pass.
        self.pcm = b"".join(struct.pack("<hh", (i * 17) % 20000 - 10000,
                                        (i * 23) % 16000 - 8000)
                            for i in range(24000))
        with wave.open(str(self.source), "wb") as audio:
            audio.setparams((2, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(self.pcm)

    def test_exact_sample_position_duration_and_audio_properties(self):
        output = extract_clip(self.source, "00:00.250", "00:01.750", self.root / "片段.wav")
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getframerate(), 8000)
            self.assertEqual(audio.getnchannels(), 2)
            self.assertEqual(audio.getnframes(), 12000)
            self.assertEqual(audio.readframes(12000), self.pcm[2000 * 4:14000 * 4])

    def test_end_at_source_duration(self):
        output = extract_clip(self.source, 2, 3, self.root / "tail.wav")
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.readframes(8000), self.pcm[16000 * 4:])

    def test_small_numeric_timestamps_reach_encoder_as_decimal_seconds(self):
        output = extract_clip(self.source, 0.000001, 0.010001, self.root / "tiny.wav")
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getnframes(), 80)

    def test_bad_ranges_do_not_create_output(self):
        output = self.root / "bad.wav"
        for start, end in [(2, 1), (1, 1), (-1, 1), (0, 3.1), (3, 4), (math.nan, 1)]:
            with self.subTest(start=start, end=end):
                with self.assertRaises(AudioError):
                    extract_clip(self.source, start, end, output)
                self.assertFalse(output.exists())

    def test_existing_output_requires_explicit_overwrite(self):
        output = self.root / "existing.wav"
        output.write_bytes(b"keep me")
        with self.assertRaises(AudioError):
            extract_clip(self.source, 0, 1, output)
        self.assertEqual(output.read_bytes(), b"keep me")
        extract_clip(self.source, 0, 1, output, overwrite=True)
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getnframes(), 8000)

    def test_cannot_overwrite_source_or_alias(self):
        original = self.source.read_bytes()
        alias = self.root / "alias.wav"
        alias.symlink_to(self.source)
        hardlink = self.root / "hardlink.wav"
        hardlink.hardlink_to(self.source)
        for output in [self.source, alias, hardlink]:
            with self.assertRaises(AudioError):
                extract_clip(self.source, 0, 1, output, overwrite=True)
        self.assertEqual(self.source.read_bytes(), original)

    def test_failed_encoder_preserves_existing_output_and_cleans_temporary(self):
        from unittest.mock import patch
        output = self.root / "existing.wav"
        output.write_bytes(b"keep me")
        real_run = subprocess.run

        def fail_encoder(command, **kwargs):
            if Path(command[0]).name == "ffmpeg":
                Path(command[-1]).write_bytes(b"partial output")
                return subprocess.CompletedProcess(command, 1, "", "encoder failed")
            return real_run(command, **kwargs)

        with patch("subprocess.run", side_effect=fail_encoder):
            with self.assertRaises(AudioError):
                extract_clip(self.source, 0, 1, output, overwrite=True)
        self.assertEqual(output.read_bytes(), b"keep me")
        self.assertEqual(list(self.root.glob(".voice-tools-*")), [])

    def test_missing_invalid_input_and_unsupported_output(self):
        invalid = self.root / "invalid.m4a"
        invalid.write_text("not audio")
        for source, output in [(self.root / "missing.wav", self.root / "x.wav"),
                               (invalid, self.root / "x.wav"),
                               (self.source, self.root / "x.txt")]:
            with self.subTest(source=source, output=output):
                with self.assertRaises(AudioError):
                    extract_clip(source, 0, 1, output)
                self.assertFalse(output.exists())

    def test_encoded_formats_and_aac_input(self):
        for suffix in [".m4a", ".mp3", ".flac"]:
            with self.subTest(suffix=suffix):
                encoded = extract_clip(self.source, 0, 2, self.root / ("encoded" + suffix))
                decoded = extract_clip(encoded, 0.25, 1.25, self.root / (suffix[1:] + ".wav"))
                with wave.open(str(decoded), "rb") as audio:
                    self.assertEqual(audio.getnframes(), 8000)
                    self.assertEqual(audio.getnchannels(), 2)
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(encoded),
                                "-f", "null", "-"], check=True, capture_output=True)

    def test_cli_default_output_and_failure_exit(self):
        project_root = Path(__file__).resolve().parents[1]
        import os
        environment = dict(os.environ, PYTHONPATH=str(project_root))
        command = [sys.executable, "-m", "voice_tools", "extract", str(self.source),
                   "--start", "0", "--end", "1"]
        result = subprocess.run(command, cwd=self.root, env=environment,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.root / "outputs" / "中文 音频_0-1.wav"
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getnframes(), 8000)
        result = subprocess.run(command, cwd=self.root, env=environment,
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
