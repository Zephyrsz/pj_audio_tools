import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from voice_tools import AudioError, Registry, ToolConfig
from voice_tools.models import Transcript
from test_openrouter_asr import make_wav


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg")
class BatchASRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "dataset"
        self.wav = self.source / "wav"
        self.wav.mkdir(parents=True)
        for name, seconds in [("short", 1), ("boundary", 2), ("keep", 2.1), ("long", 3)]:
            make_wav(self.wav / f"{name}.wav", seconds)
        (self.source / "transcript_utf8.txt").write_text("keep:旧字幕，不能用作新转写\n")
        self.output = self.root / "new_dataset"
        self.calls = 0
        self.fail_at = None
        owner = self

        class Provider:
            def transcribe(self, path, language=None):
                owner.calls += 1
                if owner.calls == owner.fail_at:
                    raise AudioError("upstream failed")
                return Transcript(" 新しい字幕。\n 次の行。 ", language)

        self.registry = Registry().register("asr", "test", lambda config: Provider())
        self.config = ToolConfig("test", "fixture")

    def run_batch(self, **kwargs):
        from voice_tools.batch_asr import transcribe_directory
        return transcribe_directory(self.source, self.output, config=self.config,
                                    registry=self.registry, **kwargs)

    def test_default_strict_duration_filter_and_utf8_export_with_original_audio(self):
        result = self.run_batch()
        self.assertEqual(result, self.output)
        self.assertEqual(self.calls, 2)
        self.assertEqual(sorted(p.name for p in (result / "wav").iterdir()), ["keep.wav", "long.wav"])
        self.assertEqual((result / "transcript_utf8.txt").read_bytes(),
                         "keep:新しい字幕。 次の行。\nlong:新しい字幕。 次の行。\n".encode())
        for name in ("keep.wav", "long.wav"):
            self.assertEqual((result / "wav" / name).read_bytes(), (self.wav / name).read_bytes())
        self.assertEqual((self.source / "transcript_utf8.txt").read_text(), "keep:旧字幕，不能用作新转写\n")
        report = json.loads((result / "batch_asr.json").read_text())
        self.assertEqual(report["status"], "complete")
        self.assertEqual(len(report["results"]), 2)

    def test_flat_wav_input_and_inclusive_upper_duration_bound(self):
        from voice_tools.batch_asr import transcribe_directory
        transcribe_directory(self.wav, self.output, config=self.config, min_duration=1,
                             max_duration=2, registry=self.registry)
        self.assertEqual(self.calls, 1)
        self.assertEqual((self.output / "transcript_utf8.txt").read_text(), "boundary:新しい字幕。 次の行。\n")

    def test_failure_checkpoint_resume_does_not_repeat_paid_successes(self):
        self.fail_at = 2
        with self.assertRaisesRegex(AudioError, "upstream failed"):
            self.run_batch()
        self.assertFalse((self.output / "transcript_utf8.txt").exists())
        self.assertEqual(len(json.loads((self.output / "batch_asr.json").read_text())["results"]), 1)
        self.fail_at = None
        self.run_batch(resume=True)
        self.assertEqual(self.calls, 3)
        self.run_batch(resume=True)
        self.assertEqual(self.calls, 3)

    def test_resume_rejects_changed_audio_settings_and_edited_export(self):
        self.run_batch()
        with self.assertRaises(AudioError):
            self.run_batch(resume=True, min_duration=1)
        make_wav(self.wav / "keep.wav", 2.2)
        with self.assertRaises(AudioError):
            self.run_batch(resume=True)
        make_wav(self.wav / "keep.wav", 2.1)
        (self.output / "transcript_utf8.txt").write_text("human correction\n")
        with self.assertRaises(AudioError):
            self.run_batch(resume=True)
        self.assertEqual(self.calls, 2)
        self.assertEqual((self.output / "transcript_utf8.txt").read_text(), "human correction\n")

    def test_invalid_filters_and_unsafe_paths_fail_before_requests(self):
        for options in ({"min_duration": -1}, {"min_duration": float("nan")},
                        {"max_duration": 2}, {"max_duration": float("inf")}):
            with self.subTest(options=options), self.assertRaises(AudioError):
                self.run_batch(**options)
        for output in (self.source, self.source / "nested", self.root):
            self.output = output
            with self.subTest(output=output), self.assertRaises(AudioError):
                self.run_batch()
        self.assertEqual(self.calls, 0)

    def test_collision_existing_output_and_invalid_wav_fail_before_requests(self):
        self.output.mkdir()
        with self.assertRaises(AudioError):
            self.run_batch()
        self.output.rmdir()
        (self.wav / "nested").mkdir()
        make_wav(self.wav / "nested" / "keep.WAV", 3)
        with self.assertRaises(AudioError):
            self.run_batch()
        (self.wav / "nested" / "keep.WAV").unlink()
        (self.wav / "keep.wav").write_bytes(b"bad wav")
        with self.assertRaises(AudioError):
            self.run_batch()
        self.assertEqual(self.calls, 0)

    def test_empty_selection_and_concurrent_resume_do_not_call_model(self):
        with self.assertRaises(AudioError):
            self.run_batch(min_duration=10)
        self.assertFalse(self.output.exists())
        self.run_batch()
        (self.output / ".batch_asr.lock").touch()
        with self.assertRaises(AudioError):
            self.run_batch(resume=True)
        self.assertTrue((self.output / ".batch_asr.lock").exists())
        self.assertEqual(self.calls, 2)

    def test_filenames_that_break_dataset_transcript_are_rejected_before_asr(self):
        for index, name in enumerate((" clip.wav", "clip .wav", "clip\\one.wav",
                                      "clip\u2028one.wav", "clip\u0085one.wav")):
            self.output = self.root / f"output_{index}"
            path = self.wav / name
            make_wav(path, 2.5)
            with self.subTest(name=name), self.assertRaises(AudioError):
                self.run_batch()
            path.unlink()
        self.assertEqual(self.calls, 0)

    def test_resume_rejects_modified_wav_and_corrupt_checkpoint(self):
        self.run_batch()
        original = (self.output / "wav" / "keep.wav").read_bytes()
        (self.output / "wav" / "keep.wav").write_bytes(b"manual edit")
        with self.assertRaises(AudioError):
            self.run_batch(resume=True)
        (self.output / "wav" / "keep.wav").write_bytes(original)
        checkpoint = self.output / "batch_asr.json"
        state = json.loads(checkpoint.read_text())
        del state["status"]
        checkpoint.write_text(json.dumps(state))
        with self.assertRaises(AudioError):
            self.run_batch(resume=True)
        for status in ([], {}):
            state["status"] = status
            checkpoint.write_text(json.dumps(state))
            with self.subTest(status=status), self.assertRaises(AudioError):
                self.run_batch(resume=True)
        self.assertEqual(self.calls, 2)

    def test_dry_run_cli_does_not_need_credentials_or_create_output(self):
        root = Path(__file__).resolve().parents[1]
        for entry in (["-m", "voice_tools.batch_asr"], ["-m", "voice_tools", "asr-batch"]):
            with self.subTest(entry=entry):
                result = subprocess.run([sys.executable, *entry, str(self.source),
                                         "-o", str(self.output), "--dry-run"],
                                        cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["selected"], 2)
                self.assertEqual(report["skipped"], 2)
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
