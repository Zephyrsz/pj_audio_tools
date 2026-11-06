import os
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

from voice_tools import AudioError, denoise_audio, restore_audio


class EnhancementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.source = self.root / "原始录音.wav"
        with wave.open(str(self.source), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(struct.pack("<h", 1234) * 8000)

    def _fake_run(self, command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        shutil.copyfile(self.source, output)
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_denoise_builds_command_and_default_output(self):
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        with patch("voice_tools.enhance.shutil.which", return_value="/usr/local/bin/speech"), \
             patch("voice_tools.enhance.subprocess.run", side_effect=self._fake_run) as run:
            output = denoise_audio(self.source, model="custom/model", chunk_seconds=30,
                                   overlap_ms=250)
        self.assertEqual(output, Path.cwd() / "outputs" / "原始录音_denoised.wav")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["/usr/local/bin/speech", "denoise", str(self.source), "-o"])
        self.assertIn("--model", command)
        self.assertIn("custom/model", command)
        self.assertIn("--chunk-seconds", command)
        self.assertIn("30", command)
        self.assertIn("--overlap-ms", command)
        self.assertIn("250", command)
        self.assertEqual(output.read_bytes(), self.source.read_bytes())

    def test_restore_forwards_variant_and_rejects_existing_output(self):
        output = self.root / "restored.wav"
        output.write_bytes(b"keep")
        with self.assertRaises(AudioError):
            restore_audio(self.source, output)
        with patch("voice_tools.enhance.shutil.which", return_value="speech"), \
             patch("voice_tools.enhance.subprocess.run", side_effect=self._fake_run) as run:
            restore_audio(self.source, output, overwrite=True, variant="int8")
        self.assertIn("--variant", run.call_args.args[0])
        self.assertIn("int8", run.call_args.args[0])
        self.assertEqual(output.read_bytes(), self.source.read_bytes())

    def test_same_input_aliases_are_rejected(self):
        for output in [self.source, self.root / "alias.wav", self.root / "hardlink.wav"]:
            if output.name == "alias.wav":
                output.symlink_to(self.source)
            elif output.name == "hardlink.wav":
                output.hardlink_to(self.source)
            with self.subTest(output=output), self.assertRaises(AudioError):
                denoise_audio(self.source, output, overwrite=True)

    def test_failed_command_removes_temp_and_preserves_existing_output(self):
        output = self.root / "clean.wav"
        output.write_bytes(b"keep")

        def fail(command, **kwargs):
            Path(command[command.index("-o") + 1]).write_bytes(b"partial")
            return subprocess.CompletedProcess(command, 1, "", "model failed")

        with patch("voice_tools.enhance.shutil.which", return_value="speech"), \
             patch("voice_tools.enhance.subprocess.run", side_effect=fail), \
             self.assertRaises(AudioError):
            denoise_audio(self.source, output, overwrite=True)
        self.assertEqual(output.read_bytes(), b"keep")
        self.assertEqual(list(self.root.glob(".voice-tools-enhance-*")), [])

    def test_non_wav_and_missing_speech_are_rejected(self):
        with self.assertRaises(AudioError):
            denoise_audio(self.source, self.root / "output.mp3")
        with patch("voice_tools.enhance.shutil.which", return_value=None), self.assertRaises(AudioError):
            denoise_audio(self.source, self.root / "output.wav")

    def test_success_without_valid_audio_does_not_publish(self):
        output = self.root / "clean.wav"
        for content in [b"", b"not a wav", self.source.read_bytes()[:-100]]:
            with self.subTest(content_length=len(content)):
                output.write_bytes(b"keep")

                def invalid(command, **kwargs):
                    Path(command[command.index("-o") + 1]).write_bytes(content)
                    return subprocess.CompletedProcess(command, 0, "", "")

                with patch("voice_tools.enhance.shutil.which", return_value="speech"), \
                     patch("voice_tools.enhance.subprocess.run", side_effect=invalid), \
                     self.assertRaises(AudioError):
                    denoise_audio(self.source, output, overwrite=True)
                self.assertEqual(output.read_bytes(), b"keep")
                self.assertEqual(list(self.root.glob(".voice-tools-enhance-*")), [])

    def test_success_without_output_is_rejected(self):
        def missing(command, **kwargs):
            Path(command[command.index("-o") + 1]).unlink(missing_ok=True)
            return subprocess.CompletedProcess(command, 0, "", "")

        output = self.root / "missing.wav"
        with patch("voice_tools.enhance.shutil.which", return_value="speech"), \
             patch("voice_tools.enhance.subprocess.run", side_effect=missing), \
             self.assertRaises(AudioError):
            denoise_audio(self.source, output)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".voice-tools-enhance-*")), [])

    def test_invalid_options_rejected_before_running_model(self):
        for options in [{"chunk_seconds": 0}, {"chunk_seconds": math.inf},
                        {"chunk_seconds": math.nan}, {"chunk_seconds": True},
                        {"overlap_ms": -1}, {"overlap_ms": 1.5},
                        {"overlap_ms": True}, {"model": ""}]:
            with self.subTest(options=options), \
                 patch("voice_tools.enhance.subprocess.run") as run, \
                 self.assertRaises(AudioError):
                denoise_audio(self.source, self.root / "bad.wav", **options)
            run.assert_not_called()
        with self.assertRaises(AudioError):
            restore_audio(self.source, self.root / "bad.wav", variant="invalid")

    def test_no_chunk_and_custom_executable(self):
        with patch("voice_tools.enhance.shutil.which", return_value="/custom/speech") as which, \
             patch("voice_tools.enhance.subprocess.run", side_effect=self._fake_run) as run:
            denoise_audio(self.source, self.root / "clean.wav",
                          executable="/custom/speech", no_chunk=True)
        which.assert_called_once_with("/custom/speech")
        self.assertIn("--no-chunk", run.call_args.args[0])

    def test_publication_race_does_not_overwrite_other_output(self):
        output = self.root / "race.wav"

        def raced(command, **kwargs):
            self._fake_run(command, **kwargs)
            output.write_bytes(b"another process")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("voice_tools.enhance.shutil.which", return_value="speech"), \
             patch("voice_tools.enhance.subprocess.run", side_effect=raced), \
             self.assertRaises(AudioError):
            denoise_audio(self.source, output)
        self.assertEqual(output.read_bytes(), b"another process")
        self.assertEqual(list(self.root.glob(".voice-tools-enhance-*")), [])

    def test_run_error_and_interruption_clean_up(self):
        output = self.root / "clean.wav"
        for error in [OSError("cannot execute"), KeyboardInterrupt()]:
            with self.subTest(error=type(error).__name__), \
                 patch("voice_tools.enhance.shutil.which", return_value="speech"), \
                 patch("voice_tools.enhance.subprocess.run", side_effect=error), \
                 self.assertRaises(AudioError if isinstance(error, OSError) else KeyboardInterrupt):
                denoise_audio(self.source, output)
            self.assertFalse(output.exists())
            self.assertEqual(list(self.root.glob(".voice-tools-enhance-*")), [])

    def test_missing_source_directory_and_broken_output_link(self):
        with self.assertRaises(AudioError):
            denoise_audio(self.root / "absent.wav", self.root / "out.wav")
        directory = self.root / "directory.wav"
        directory.mkdir()
        with self.assertRaises(AudioError):
            denoise_audio(self.source, directory, overwrite=True)
        broken = self.root / "broken.wav"
        broken.symlink_to(self.root / "absent.wav")
        with self.assertRaises(AudioError):
            denoise_audio(self.source, broken)
        blocker = self.root / "file"
        blocker.write_text("not a directory")
        with self.assertRaises(AudioError):
            denoise_audio(self.source, blocker / "out.wav")

    def test_cleanup_failure_does_not_mask_processing_error(self):
        output = self.root / "clean.wav"
        output.write_bytes(b"keep")
        failure = subprocess.CompletedProcess([], 1, "", "specific model failure")
        with patch("voice_tools.enhance.shutil.which", return_value="speech"), \
             patch("voice_tools.enhance.subprocess.run", return_value=failure), \
             patch("voice_tools.enhance.Path.unlink", side_effect=PermissionError("cleanup blocked")), \
             self.assertRaisesRegex(AudioError, "specific model failure"):
            denoise_audio(self.source, output, overwrite=True)
        self.assertEqual(output.read_bytes(), b"keep")

    def test_cleanup_failure_after_success_is_audio_error(self):
        output = self.root / "clean.wav"
        with patch("voice_tools.enhance.shutil.which", return_value="speech"), \
             patch("voice_tools.enhance.subprocess.run", side_effect=self._fake_run), \
             patch("voice_tools.enhance.Path.unlink", side_effect=PermissionError("cleanup blocked")), \
             self.assertRaisesRegex(AudioError, "输出已保存"):
            denoise_audio(self.source, output)
        self.assertEqual(output.read_bytes(), self.source.read_bytes())

    def test_cleanup_failure_preserves_interruption(self):
        with patch("voice_tools.enhance.shutil.which", return_value="speech"), \
             patch("voice_tools.enhance.subprocess.run", side_effect=KeyboardInterrupt), \
             patch("voice_tools.enhance.Path.unlink", side_effect=PermissionError("cleanup blocked")), \
             self.assertRaises(KeyboardInterrupt):
            denoise_audio(self.source, self.root / "clean.wav")


class EnhancementCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.source = self.root / "有 空格的录音.wav"
        with wave.open(str(self.source), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(struct.pack("<h", 1234) * 8000)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.speech = self.bin / "speech"
        self.speech.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, shutil, sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "Path(os.environ['TEST_SPEECH_LOG']).write_text(json.dumps(args))\n"
            "if os.environ.get('TEST_SPEECH_FAIL'):\n"
            "    print('model download failed', file=sys.stderr)\n"
            "    sys.exit(7)\n"
            "shutil.copyfile(args[1], args[args.index('-o') + 1])\n"
        )
        self.speech.chmod(0o755)
        self.log = self.root / "arguments.json"
        project_root = Path(__file__).resolve().parents[1]
        self.environment = dict(os.environ, PYTHONPATH=str(project_root),
                                PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                                TEST_SPEECH_LOG=str(self.log))

    def cli(self, *arguments, environment=None):
        return subprocess.run([sys.executable, "-m", "voice_tools", *arguments],
                              cwd=self.root, env=environment or self.environment,
                              text=True, capture_output=True)

    def test_both_commands_write_valid_audio_and_use_original_source(self):
        for operation, suffix in [("denoise", "denoised"), ("restore", "restored")]:
            with self.subTest(operation=operation):
                result = self.cli(operation, str(self.source))
                self.assertEqual(result.returncode, 0, result.stderr)
                output = self.root / "outputs" / f"{self.source.stem}_{suffix}.wav"
                with wave.open(str(output), "rb") as audio:
                    self.assertEqual(audio.getnframes(), 8000)
                    self.assertEqual(audio.readframes(8000), struct.pack("<h", 1234) * 8000)
                self.assertIn(str(output), result.stdout)
                arguments = json.loads(self.log.read_text())
                self.assertEqual(arguments[:2], [operation, str(self.source)])
                temporary = Path(arguments[arguments.index("-o") + 1])
                self.assertEqual(temporary.parent, output.parent)
                self.assertFalse(temporary.exists())
                self.assertEqual(list(output.parent.glob(".voice-tools-enhance-*")), [])

    def test_cli_forwards_denoise_and_restore_options(self):
        denoised = self.root / "nested" / "清理.wav"
        result = self.cli("denoise", str(self.source), "-o", str(denoised),
                          "--model", "owner/model", "--chunk-seconds", "30",
                          "--overlap-ms", "250", "--no-chunk")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(self.log.read_text())
        self.assertEqual(arguments[arguments.index("--model") + 1], "owner/model")
        self.assertEqual(float(arguments[arguments.index("--chunk-seconds") + 1]), 30)
        self.assertEqual(arguments[arguments.index("--overlap-ms") + 1], "250")
        self.assertIn("--no-chunk", arguments)
        restored = self.root / "修复.wav"
        result = self.cli("restore", str(self.source), "-o", str(restored),
                          "--model", "owner/restore", "--variant", "int8")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(self.log.read_text())
        self.assertEqual(arguments[arguments.index("--variant") + 1], "int8")
        self.assertEqual(arguments[arguments.index("--model") + 1], "owner/restore")

    def test_cli_errors_preserve_output_and_have_no_traceback(self):
        output = self.root / "keep.wav"
        output.write_bytes(b"keep")
        for operation in ["denoise", "restore"]:
            result = self.cli(operation, str(self.source), "-o", str(output))
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(output.read_bytes(), b"keep")
            environment = dict(self.environment, TEST_SPEECH_FAIL="1")
            result = self.cli(operation, str(self.source), "-o", str(output),
                              "--overwrite", environment=environment)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("model download failed", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(output.read_bytes(), b"keep")
        result = self.cli("denoise", str(self.source), "-o", str(output), "--overwrite")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), self.source.read_bytes())


if __name__ == "__main__":
    unittest.main()
