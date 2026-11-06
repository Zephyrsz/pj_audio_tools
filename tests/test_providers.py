import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch
import wave

from voice_tools import AudioError, Registry, ToolConfig, complete_text, default_registry, synthesize_text, transcribe_audio
from voice_tools.providers.command import CommandLLM
from voice_tools.providers.soniqo import SoniqoASR, SoniqoAligner
from voice_tools.segmentation import align_units, llm_boundaries
from voice_tools.models import TimedWord


def audio_bytes():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack("<h", 900) * 1600)
    return stream.getvalue()


class ProviderTests(unittest.TestCase):
    def test_registry_is_function_scoped_and_extensible(self):
        registry = Registry()
        registry.register("asr", "mine", lambda config: config.model)
        registry.register("tts", "mine", lambda config: config.model)
        self.assertEqual(registry.create("asr", ToolConfig("mine", "small")), "small")
        self.assertEqual(registry.create("tts", ToolConfig("mine", "large")), "large")
        with self.assertRaises(AudioError):
            registry.register("asr", "mine", lambda config: None)
        with self.assertRaises(AudioError):
            registry.create("llm", ToolConfig("mine"))
        self.assertIn("command", default_registry().available()["asr"])

    def test_secrets_are_environment_references(self):
        with self.assertRaisesRegex(AudioError, "环境变量"):
            ToolConfig("openai-compatible", "model", {"api_key": "do-not-store"})

    def test_command_protocol_uses_stdin_json_and_propagates_model(self):
        code = (
            "import json,sys; r=json.load(sys.stdin); "
            "print(json.dumps({'text':r['model']+' '+r['messages'][0]['content']}))"
        )
        config = ToolConfig("command", "local-model", {"argv": [sys.executable, "-c", code]})
        self.assertEqual(complete_text("hello; $HOME", config=config), "local-model hello; $HOME")
        bad = ToolConfig("command", "local", {"argv": [sys.executable, "-c", "print('not JSON')"]})
        with self.assertRaises(AudioError):
            CommandLLM(bad).complete([])

    def test_llm_schema_cannot_rewrite_or_omit(self):
        units = align_units("Hello. Yes.", [TimedWord("Hello", 0, .4), TimedWord("Yes", .5, 1)], 1)

        class LLM:
            def complete(self, messages, **kwargs):
                return '{"end_unit_ids":[1,2],"text":"rewritten"}'

        with self.assertRaisesRegex(AudioError, "只能返回"):
            llm_boundaries(units, LLM())

    def test_soniqo_aligner_model_and_language_are_explicit(self):
        adapter = SoniqoAligner(ToolConfig("soniqo", "custom-aligner", {"engine": "coreml"}))
        with patch.object(adapter, "run", return_value="Loading...\n[0.10s - 0.50s] こんにちは。\n") as run:
            words = adapter.align(Path("audio.wav"), "こんにちは。", "Japanese")
        command = run.call_args.args[0]
        self.assertIn("--aligner-model", command)
        self.assertIn("custom-aligner", command)
        self.assertEqual(words[0].text, "こんにちは。")
        with self.assertRaises(AudioError):
            adapter.align(Path("audio.wav"), "こんにちは。")

    def test_soniqo_batch_reads_text_files_not_mixed_stdout(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "input.wav"
            path.write_bytes(audio_bytes())
            adapter = SoniqoASR(ToolConfig("soniqo", "1.7B"))

            def fake(command):
                directory = Path(command[command.index("--output-dir") + 1])
                directory.mkdir()
                (directory / "000000.txt").write_text('一行目。\n二行目。')
                return "lots of logs {bad JSON}"

            with patch.object(adapter, "run", side_effect=fake):
                result = adapter.transcribe_many([path], "Japanese")
            self.assertEqual(result[0].text, "一行目。\n二行目。")


@unittest.skipUnless(shutil.which("ffmpeg"), "需要 FFmpeg")
class HTTPProviderTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests.append((self.path, dict(self.headers), body))
                if self.path.startswith("/v1/bad/"):
                    self.send_error(404)
                    return
                if self.path.endswith("/audio/transcriptions"):
                    payload = json.dumps({"text": "こんにちは。"}).encode()
                elif self.path.endswith("/chat/completions"):
                    payload = json.dumps({"choices": [
                        {"message": {"content": "LLM response"}, "finish_reason": "stop"}]}).encode()
                elif self.path.endswith("/audio/speech"):
                    payload = audio_bytes()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / "input.wav"
        self.input.write_bytes(audio_bytes())
        self.options = {"base_url": f"http://127.0.0.1:{self.server.server_port}/v1", "api_key_env": None}

    def spec(self, model):
        return ToolConfig("openai-compatible", model, self.options)

    def test_asr_llm_tts_switch_models_over_local_http(self):
        result = transcribe_audio(self.input, config=self.spec("asr-a"), language="ja")
        self.assertEqual(result.text, "こんにちは。")
        self.assertIn(b"asr-a", self.requests[-1][2])
        self.assertIn(b'name="file"', self.requests[-1][2])
        self.assertEqual(complete_text("prompt", config=self.spec("llm-b")), "LLM response")
        self.assertEqual(json.loads(self.requests[-1][2])["model"], "llm-b")
        output = synthesize_text("hello", self.root / "tts.wav", config=self.spec("tts-c"))
        self.assertEqual(json.loads(self.requests[-1][2])["model"], "tts-c")
        with wave.open(str(output)) as audio:
            self.assertEqual(audio.getnframes(), 1600)
        with self.assertRaises(AudioError):
            synthesize_text("hello", output, config=self.spec("tts-c"))

    def test_http_failure_does_not_publish_or_expose_token(self):
        options = {**self.options, "base_url": self.options["base_url"] + "/bad",
                   "api_key_env": "TEST_VOICE_TOOLS_KEY"}
        with patch.dict(os.environ, TEST_VOICE_TOOLS_KEY="private-token"):
            with self.assertRaises(AudioError) as error:
                synthesize_text("hello", self.root / "failure.wav",
                                config=ToolConfig("openai-compatible", "tts", options))
        self.assertNotIn("private-token", str(error.exception))
        self.assertTrue(error.exception.__cause__.closed)
        self.assertFalse((self.root / "failure.wav").exists())

    def test_cli_http_llm_and_provider_list(self):
        config = self.root / "llm.json"
        config.write_text(json.dumps({"provider": "openai-compatible", "model": "cli-model", "options": self.options}))
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-m", "voice_tools", "llm",
                                 "--text", "hello", "--config", str(config)],
                                cwd=root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "LLM response")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg")
class CommandCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(audio_bytes())
        worker = self.root / "worker.py"
        worker.write_text(
            "import json,sys,wave\n"
            "r=json.load(sys.stdin)\n"
            "if r['kind']=='asr':\n"
            " result={'text':r['model']+'.','language':r['language']}\n"
            "elif r['kind']=='aligner':\n"
            " result={'words':[{'text':r['text'],'start':0.01,'end':0.08}]}\n"
            "elif r['kind']=='vad':\n"
            " result={'spans':[{'start':0.01,'end':0.09}]}\n"
            "elif r['kind']=='tts':\n"
            " with wave.open(r['output_path'],'wb') as w:\n"
            "  w.setparams((1,2,16000,0,'NONE','not compressed'))\n"
            "  w.writeframes(b'\\0\\0'*1600)\n"
            " result={'ok':True}\n"
            "print(json.dumps(result))\n", encoding="utf-8")
        self.config = self.root / "tool.json"
        self.config.write_text(json.dumps({"provider": "command", "model": "old-model",
            "options": {"argv": [sys.executable, str(worker)]}}))

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "voice_tools", *args],
                              cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True)

    def test_standalone_audio_commands_use_config_and_model_override(self):
        result = self.cli("asr", str(self.source), "--config", str(self.config),
                          "--model", "new-model", "--language", "ja")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["text"], "new-model.")
        result = self.cli("align", str(self.source), "--text", "Hello.",
                          "--config", str(self.config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["units"][0]["text"], "Hello.")
        result = self.cli("vad", str(self.source), "--config", str(self.config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["spans"], [{"start": .01, "end": .09}])
        output = self.root / "tts.wav"
        result = self.cli("tts", "--text", "Hello.", "--config", str(self.config), "-o", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        with wave.open(str(output)) as audio:
            self.assertEqual(audio.getnframes(), 1600)

    def test_audio_commands_cannot_overwrite_source_even_with_overwrite(self):
        before = self.source.read_bytes()
        for kind in ("asr", "align", "vad"):
            args = [kind, str(self.source), "--config", str(self.config),
                    "-o", str(self.source), "--overwrite"]
            if kind == "align":
                args += ["--text", "Hello."]
            result = self.cli(*args)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(self.source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
