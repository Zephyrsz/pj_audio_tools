from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
import wave

from voice_tools import AudioError, PipelineConfig, Registry, ToolConfig, split_sentences
from voice_tools.models import Span, TimedWord, Transcript, Unit
from voice_tools.pipeline import merge_units, plan_windows
from voice_tools.segmentation import align_units, llm_boundaries
from voice_tools.storage import file_hash


def wav(path, seconds=1, rate=8000):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack("<h", 1500) * round(seconds * rate))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg")
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "原始.wav"
        wav(self.source, 12)
        self.output = self.root / "task"
        self.calls = {"asr": [], "aligner": [], "vad": [], "llm": [], "tts": []}
        calls = self.calls

        class ASR:
            def __init__(self, config):
                self.config = config

            def transcribe_many(self, paths, language):
                calls["asr"].append((self.config.model, len(paths)))
                first = "こんばんは" if self.config.model == "b" else "こんにちは"
                return [Transcript(first + "。元気です！", language) for _ in paths]

        class Aligner:
            def __init__(self, config):
                self.config = config

            def align(self, path, text, language):
                calls["aligner"].append(self.config.model)
                if self.config.model == "broken":
                    return [TimedWord("こんにちは", .2, .8)]
                a, b = text.split("。")
                return [TimedWord(a, .2, .8), TimedWord(b.rstrip("！"), 1, 1.5)]

        class VAD:
            def __init__(self, config):
                pass

            def detect(self, path):
                calls["vad"].append(1)
                with wave.open(str(path)) as audio:
                    total = audio.getnframes() / audio.getframerate()
                return [Span(0, total)]

        class LLM:
            def __init__(self, config):
                self.config = config

            def complete(self, messages, *, json_mode):
                calls["llm"].append(self.config.model)
                units = json.loads(messages[-1]["content"])["units"]
                if self.config.model == "broken":
                    return '{"end_unit_ids":[999]}'
                return json.dumps({"end_unit_ids": [len(units)]})

        class TTS:
            def __init__(self, config):
                self.config = config

            def synthesize(self, text, output, language):
                calls["tts"].append(self.config.model)
                if self.config.model == "broken":
                    raise AudioError("TTS failure")
                wav(output)

        self.registry = Registry()
        for kind, factory in [("asr", ASR), ("aligner", Aligner), ("vad", VAD), ("llm", LLM), ("tts", TTS)]:
            self.registry.register(kind, "fake", factory)
        self.config = PipelineConfig(
            language="Japanese", asr=ToolConfig("fake", "a"),
            aligner=ToolConfig("fake", "a"), vad=ToolConfig("fake", "a"),
            window_seconds=4, context_seconds=0, boundary_search_seconds=0)

    def run_pipeline(self, config=None, **kwargs):
        return split_sentences(self.source, self.output, config=config or self.config,
                               registry=self.registry, start=2, end=10, **kwargs)

    def manifest(self):
        return json.loads((self.output / "dataset/manifest.json").read_text())

    def test_exports_source_timeline_clip_timeline_and_dataset_pairs(self):
        before = file_hash(self.source)
        result = self.run_pipeline()
        self.assertEqual(result, self.output / "dataset")
        manifest = self.manifest()
        self.assertEqual(manifest["count"], 4)
        self.assertEqual(manifest["sentences"][0]["speech_start"], 2.2)
        self.assertEqual(manifest["sentences"][2]["speech_start"], 6.2)
        self.assertIn("00:00:02,200", (result / "subtitles.srt").read_text())
        self.assertNotIn("00:00:02,200", (result / "srt/000001.srt").read_text())
        lines = (result / "transcript_utf8.txt").read_text().splitlines()
        self.assertEqual(len(lines), len(list((result / "wav").glob("*.wav"))))
        self.assertTrue(all(line.split(":", 1)[0].isdigit() for line in lines))
        self.assertEqual(file_hash(self.source), before)
        self.assertFalse((self.output / ".lock").exists())

    def test_resume_reuses_models_and_changing_llm_only_resegments(self):
        self.run_pipeline()
        counts = {k: len(v) for k, v in self.calls.items()}
        self.run_pipeline(resume=True)
        self.assertEqual({k: len(v) for k, v in self.calls.items()}, counts)
        llm_config = replace(self.config, llm=ToolConfig("fake", "one"))
        self.run_pipeline(llm_config, resume=True)
        self.assertEqual(len(self.calls["asr"]), counts["asr"])
        self.assertEqual(len(self.calls["aligner"]), counts["aligner"])
        self.assertEqual(len(self.calls["llm"]), 1)
        self.assertEqual(self.manifest()["count"], 1)
        self.run_pipeline(llm_config, resume=True)
        self.assertEqual(len(self.calls["llm"]), 1)
        self.run_pipeline(replace(llm_config, llm=ToolConfig("fake", "two")), resume=True)
        self.assertEqual(len(self.calls["llm"]), 2)

    def test_asr_model_change_invalidates_text_and_alignment(self):
        self.run_pipeline()
        self.run_pipeline(replace(self.config, asr=ToolConfig("fake", "b")), resume=True)
        self.assertEqual(len(self.calls["asr"]), 2)
        self.assertEqual(len(self.calls["aligner"]), 4)
        self.assertIn("こんばんは", (self.output / "dataset/transcript_utf8.txt").read_text())

    def test_partial_failure_keeps_previous_dataset_and_releases_lock(self):
        self.run_pipeline()
        original = (self.output / "dataset/manifest.json").read_bytes()
        for config in [
            replace(self.config, aligner=ToolConfig("fake", "broken")),
            replace(self.config, llm=ToolConfig("fake", "broken")),
            replace(self.config, tts=ToolConfig("fake", "broken")),
        ]:
            with self.subTest(config=config), self.assertRaises(AudioError):
                self.run_pipeline(config, resume=True)
            self.assertEqual((self.output / "dataset/manifest.json").read_bytes(), original)
            self.assertFalse((self.output / ".lock").exists())
            self.assertEqual(list(self.output.glob(".dataset-*")), [])
        self.run_pipeline(resume=True)

    def test_tts_is_optional_separate_and_cached_by_text_and_model(self):
        self.run_pipeline(replace(self.config, tts=ToolConfig("fake", "voice-a")))
        # Repeated identical sentences reuse the same synthesis result.
        self.assertEqual(len(self.calls["tts"]), 2)
        self.assertEqual(len(list((self.output / "dataset/tts").glob("*.wav"))), 4)
        self.run_pipeline(replace(self.config, tts=ToolConfig("fake", "voice-a")), resume=True)
        self.assertEqual(len(self.calls["tts"]), 2)
        self.run_pipeline(replace(self.config, tts=ToolConfig("fake", "voice-b")), resume=True)
        self.assertEqual(len(self.calls["tts"]), 4)
        self.assertEqual(len(self.calls["asr"]), 1)

    def test_tts_script_change_rebuilds_generated_audio_without_retranscribing(self):
        worker = self.root / "tts_worker.py"
        template = (
            "import json,sys,struct,wave\n"
            "r=json.load(sys.stdin)\n"
            "with wave.open(r['output_path'],'wb') as w:\n"
            " w.setparams((1,2,8000,0,'NONE','not compressed'))\n"
            " w.writeframes(struct.pack('<h',AMPLITUDE)*8000)\n"
            "print(json.dumps({'ok':True}))\n"
        )
        worker.write_text(template.replace("AMPLITUDE", "1000"))
        config = replace(self.config, tts=ToolConfig("command", "voice", {
            "argv": [sys.executable, str(worker)]}))
        # Keep the default command provider alongside the test model registry.
        from voice_tools.providers.command import CommandTTS
        self.registry.register("tts", "command", CommandTTS)
        self.run_pipeline(config)
        old = file_hash(self.output / "dataset/tts/000001.wav")
        stat = worker.stat()
        worker.write_text(template.replace("AMPLITUDE", "2000"))
        os.utime(worker, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        self.run_pipeline(config, resume=True)
        self.assertNotEqual(file_hash(self.output / "dataset/tts/000001.wav"), old)
        self.assertEqual(len(self.calls["asr"]), 1)

    def test_tts_reference_content_change_invalidates_synthesis_and_export(self):
        reference = self.root / "reference.wav"
        wav(reference)

        class ReferenceTTS:
            def __init__(self, config):
                self.reference = Path(config.options["voice_sample"])

            def synthesize(self, text, output, language):
                shutil.copyfile(self.reference, output)

        self.registry.register("tts", "reference", ReferenceTTS)
        config = replace(self.config, tts=ToolConfig("reference", "voice", {
            "voice_sample": str(reference)}))
        self.run_pipeline(config)
        old = file_hash(self.output / "dataset/tts/000001.wav")
        stat = reference.stat()
        with wave.open(str(reference), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(struct.pack("<h", 2500) * 8000)
        os.utime(reference, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.run_pipeline(config, resume=True)
        self.assertNotEqual(file_hash(self.output / "dataset/tts/000001.wav"), old)
        self.assertEqual(len(self.calls["asr"]), 1)

    def test_cache_tampering_fails_closed(self):
        self.run_pipeline()
        entry = next((self.output / "cache/asr").glob("*.json"))
        data = json.loads(entry.read_text())
        data["data"]["text"] = "changed"
        entry.write_text(json.dumps(data))
        with self.assertRaisesRegex(AudioError, "缓存损坏"):
            self.run_pipeline(resume=True)

    def test_manual_dataset_edits_are_not_overwritten_on_resume(self):
        self.run_pipeline()
        transcript = self.output / "dataset/transcript_utf8.txt"
        transcript.write_text("000001:手工校对后的字幕\n")
        with self.assertRaisesRegex(AudioError, "已有修改"):
            self.run_pipeline(resume=True)
        self.assertEqual(transcript.read_text(), "000001:手工校对后的字幕\n")

    def test_output_source_and_task_ownership_protection(self):
        with self.assertRaises(AudioError):
            split_sentences(self.source, self.root, config=self.config, registry=self.registry)
        self.output.mkdir()
        (self.output / "keep").write_text("keep")
        with self.assertRaises(AudioError):
            self.run_pipeline()
        self.assertEqual((self.output / "keep").read_text(), "keep")

    def test_resume_rejects_source_change_and_concurrent_run(self):
        self.run_pipeline()
        (self.output / ".lock").write_text("another")
        with self.assertRaisesRegex(AudioError, "正被使用"):
            self.run_pipeline(resume=True)
        self.assertEqual((self.output / ".lock").read_text(), "another")
        (self.output / ".lock").unlink()
        with self.assertRaisesRegex(AudioError, "不同"):
            split_sentences(self.source, self.output, config=self.config, registry=self.registry,
                            start=0, end=10, resume=True)

    def test_configuration_validation_and_window_coverage(self):
        for values in [{"window_seconds": float("nan")}, {"channel": True},
                       {"skip_silence": 1}, {"asr_group_size": 0},
                       {"context_seconds": 12}]:
            with self.subTest(values=values), self.assertRaises(AudioError):
                replace(self.config, **values)
        with self.assertRaises(AudioError):
            PipelineConfig.from_dict({"typo": 1})
        windows = plan_windows(13, [Span(1, 2), Span(6, 8)], self.config)
        self.assertEqual(windows[0]["core_start"], 0)
        self.assertEqual(windows[-1]["core_end"], 13)
        self.assertTrue(all(a["core_end"] == b["core_start"] for a, b in zip(windows, windows[1:])))

    def test_short_tail_is_merged_with_previous_window(self):
        config = replace(self.config, window_seconds=24, context_seconds=.6)
        windows = plan_windows(100, [Span(0, 100)], config)
        self.assertEqual(len(windows), 4)
        self.assertEqual(windows[-1]["core_start"], 72)
        self.assertEqual(windows[-1]["core_end"], 100)

    def test_outside_tail_text_is_audited_without_fabricated_audio(self):
        class TailASR:
            def transcribe(self, path, language):
                return Transcript("はい。余分。", language)

        class TailAligner:
            def align(self, path, text, language):
                with wave.open(str(path)) as audio:
                    total = audio.getnframes() / audio.getframerate()
                return [TimedWord("はい", .2, .8),
                        TimedWord("余分", total + .1, total + .2)]

        self.registry.register("asr", "tail", lambda config: TailASR())
        self.registry.register("aligner", "tail", lambda config: TailAligner())
        config = replace(self.config, asr=ToolConfig("tail"), aligner=ToolConfig("tail"))
        dataset = self.run_pipeline(config)
        manifest = self.manifest()
        self.assertEqual(manifest["count"], 2)
        self.assertEqual(manifest["unassigned_tail_count"], 2)
        self.assertEqual([s["text"] for s in manifest["sentences"]], ["はい。", "はい。"])
        self.assertIn("unassigned_tail_text", manifest["sentences"][-1]["flags"])
        issues = json.loads((dataset / "alignment_issues.json").read_text())
        self.assertEqual([r["text"] for r in issues], ["余分。", "余分。"])
        self.assertAlmostEqual(issues[-1]["start"], 10.1)
        alignment = json.loads((self.output / "alignment.json").read_text())
        self.assertEqual(alignment["unassigned_tail"], issues)
        self.assertIn("余分", (self.output / "transcript.raw.json").read_text())
        self.assertNotIn("余分", (dataset / "transcript_utf8.txt").read_text())

    def test_context_tail_overrun_does_not_block_next_windows_owned_text(self):
        class ContextASR:
            def transcribe(self, path, language):
                return Transcript("はい。尾文。" if path.stem.endswith("000000") else "尾文。次。", language)

        class ContextAligner:
            def align(self, path, text, language):
                if text.startswith("はい"):
                    return [TimedWord("はい", .2, .8), TimedWord("尾文", 4.24, 5.04)]
                return [TimedWord("尾文", .84, 1.64), TimedWord("次", 2, 2.4)]

        self.registry.register("asr", "context", lambda config: ContextASR())
        self.registry.register("aligner", "context", lambda config: ContextAligner())
        config = replace(self.config, context_seconds=.6,
                         asr=ToolConfig("context"), aligner=ToolConfig("context"))
        dataset = self.run_pipeline(config)
        self.assertEqual([s["text"] for s in self.manifest()["sentences"]], ["はい。", "尾文。", "次。"])
        issues = json.loads((dataset / "alignment_issues.json").read_text())
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["reason"], "context_tail_exceeds_window_audio")
        self.assertEqual(issues[0]["text"], "尾文。")
        self.assertAlmostEqual(issues[0]["end"], 7.04)
        diagnostic = json.loads((self.output / "logs/align-window-000000.json").read_text())
        self.assertEqual(diagnostic["words"][-1]["end"], 5.04)
        self.run_pipeline(config, resume=True)

    def test_bad_owned_alignment_reports_window_and_preserves_raw_diagnostic(self):
        with self.assertRaisesRegex(AudioError, "窗口 0.*原始结果"):
            self.run_pipeline(replace(self.config, aligner=ToolConfig("fake", "broken")))
        diagnostic = self.output / "logs/align-window-000000.json"
        self.assertEqual(json.loads(diagnostic.read_text())["words"][0]["text"], "こんにちは")
        self.assertFalse((self.output / "dataset").exists())
        self.assertEqual(list((self.output / "cache").glob("align/*.json")), [])

    def test_large_boundary_overlap_fails_instead_of_retiming_words(self):
        with self.assertRaisesRegex(AudioError, "冲突"):
            merge_units([
                ({"core_start": 0, "core_end": 1}, [Unit(1, "A", 0, 1)]),
                ({"core_start": 1, "core_end": 2}, [Unit(1, "B", .9, 1.3)]),
            ])

    def test_merge_preserves_unspaced_ascii_terms_inside_one_transcript(self):
        units = align_units("ChatGPTです。", [TimedWord("Chat", .1, .4),
            TimedWord("GPT", .4, .7), TimedWord("です", .7, 1)], 2)
        merged = merge_units([({"core_start": 0, "core_end": 2}, units)])
        self.assertEqual("".join(u.text for u in merged), "ChatGPTです。")

    def test_cross_window_word_join_survives_deduplicated_first_word(self):
        merged = merge_units([
            ({"core_start": 0, "core_end": 1}, [Unit(1, "Hello", .7, 1.1)]),
            ({"core_start": 1, "core_end": 2}, [
                Unit(1, "Hello ", .8, 1.3), Unit(2, "world.", 1.3, 1.8)]),
        ])
        self.assertEqual("".join(u.text for u in merged), "Hello world.")

    def test_overlap_rounding_and_missing_boundary_word_are_reviewable(self):
        windows = [
            {"core_start": 0, "core_end": 1, "start": 0, "end": 1.2},
            {"core_start": 1, "core_end": 2, "start": .8, "end": 2},
        ]
        merged = merge_units([
            (windows[0], [Unit(1, "A", 0, 1)]),
            (windows[1], [Unit(1, "B", .96, 1.2), Unit(2, "C", 1.3, 1.8)]),
        ])
        self.assertEqual("".join(u.text for u in merged).replace(" ", ""), "ABC")
        self.assertEqual(merged[0].end, merged[1].start)
        self.assertIn("alignment_overlap_adjusted", merged[1].flags)
        rescued = merge_units([
            (windows[0], [Unit(1, "A", 0, .8)]),
            (windows[1], [Unit(1, "B", .85, 1.1), Unit(2, "C", 1.3, 1.8)]),
        ])
        self.assertEqual("".join(u.text for u in rescued).replace(" ", ""), "ABC")
        self.assertIn("window_boundary_rescued", rescued[1].flags)


if __name__ == "__main__":
    unittest.main()
