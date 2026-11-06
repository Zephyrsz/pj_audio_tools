import json
from pathlib import Path
import tempfile
import unittest

from voice_tools import AudioError, ToolConfig
from voice_tools.cache_keys import PIPELINE_VERSION, provider_key
from voice_tools.models import Sentence
from voice_tools.timeline import refine_edges
from voice_tools.workspace import open_task


def sentence(speech_start, speech_end, cut_start, cut_end):
    return Sentence(id="000001", text="x", first_unit=1, last_unit=1,
                    speech_start=speech_start, speech_end=speech_end,
                    cut_start=cut_start, cut_end=cut_end)


class RefineEdgesTests(unittest.TestCase):
    def test_moves_cut_to_nearest_quiet_frame_outside_speech(self):
        frames = [(0.90, 0.92, -60), (1.00, 1.02, -20), (2.04, 2.06, -60)]
        [result] = refine_edges([sentence(1.0, 2.0, 0.95, 2.1)], frames)
        self.assertAlmostEqual(result.cut_start, 0.91)
        self.assertAlmostEqual(result.cut_end, 2.05)

    def test_loud_frames_keep_nominal_cuts(self):
        [result] = refine_edges([sentence(1.0, 2.0, 0.95, 2.1)], [(0.9, 0.92, -10)])
        self.assertEqual((result.cut_start, result.cut_end), (0.95, 2.1))

    def test_overlapping_neighbours_split_at_speech_midpoint(self):
        a, b = refine_edges([sentence(1.0, 2.0, 0.9, 2.3), sentence(2.2, 3.0, 1.9, 3.1)], [])
        self.assertAlmostEqual(a.cut_end, 2.1)
        self.assertAlmostEqual(b.cut_start, 2.1)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(b"x")
        self.task = self.root / "task"
        self.identity = {"path": str(self.source), "start": 0}

    def test_new_task_writes_marker_logs_stages_and_releases_lock(self):
        seen = []
        with open_task(self.task, self.source, self.identity, progress=seen.append) as workspace:
            self.assertTrue((self.task / ".lock").exists())
            with workspace.stage("vad", tool="fake"):
                pass
            with self.assertRaises(AudioError):
                with workspace.stage("asr"):
                    raise AudioError("boom")
        self.assertFalse((self.task / ".lock").exists())
        self.assertEqual(seen, ["vad", "asr"])
        marker = json.loads((self.task / "task.json").read_text())
        self.assertEqual(marker["source"], self.identity)
        rows = [json.loads(line) for line in (self.task / "logs/process.jsonl").read_text().splitlines()]
        self.assertEqual([(r["stage"], r["status"]) for r in rows],
                         [("vad", "started"), ("vad", "completed"), ("asr", "started"), ("asr", "failed")])
        self.assertEqual(rows[-1]["error"], "boom")

    def test_non_empty_without_resume_concurrent_and_mismatched_resume_fail(self):
        with open_task(self.task, self.source, self.identity):
            pass
        with self.assertRaisesRegex(AudioError, "非空"):
            with open_task(self.task, self.source, self.identity):
                pass
        with self.assertRaisesRegex(AudioError, "不同"):
            with open_task(self.task, self.source, {**self.identity, "start": 1}, resume=True):
                pass
        (self.task / ".lock").write_text("1")
        with self.assertRaisesRegex(AudioError, "正被使用"):
            with open_task(self.task, self.source, self.identity, resume=True):
                pass

    def test_task_containing_source_is_rejected(self):
        with self.assertRaises(AudioError):
            with open_task(self.root, self.source, self.identity):
                pass


class ProviderKeyTests(unittest.TestCase):
    def test_key_tracks_config_adapter_and_command_file(self):
        class Adapter:
            pass

        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "tool.sh"
            script.write_text("echo 1")
            spec = ToolConfig("command", "m", {"argv": [str(script)]})
            key = provider_key("asr", spec, Adapter())
            self.assertEqual(key["pipeline_version"], PIPELINE_VERSION)
            self.assertTrue(key["adapter"].endswith("Adapter"))
            self.assertEqual(key["tools"][0][0], str(script.resolve()))
            script.write_text("echo 22")
            self.assertNotEqual(provider_key("asr", spec, Adapter())["tools"], key["tools"])

    def test_tts_voice_sample_must_be_path(self):
        with self.assertRaises(AudioError):
            provider_key("tts", ToolConfig("x", None, {"voice_sample": 1}), object())


if __name__ == "__main__":
    unittest.main()
