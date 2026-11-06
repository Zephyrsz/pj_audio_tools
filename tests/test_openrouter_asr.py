import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

from voice_tools import AudioError, ToolConfig, default_registry


def make_wav(path, seconds=.1):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * round(seconds * 16000))


class OpenRouterASRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.audio = self.root / "input.wav"
        make_wav(self.audio)
        self.key = self.root / "or.cfg"
        self.key.write_text('\ufeff# local credential\nexport OPENROUTER_API_KEY="file-secret"\n')
        self.requests = []
        self.status, self.response = 200, {"text": "こんにちは。\n次の行。"}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.requests.append((self.path, dict(self.headers),
                                       json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(owner.status)
                if owner.status == 302:
                    self.send_header("Location", "/redirected")
                self.end_headers()
                body = owner.response
                self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.options = {"base_url": f"http://127.0.0.1:{self.server.server_port}/v1",
                        "api_key_file": str(self.key), "api_key_env": "VOICE_TEST_OR_KEY"}

    def provider(self, **options):
        return default_registry().create("asr", ToolConfig("openrouter", options={**self.options, **options}))

    def test_file_key_json_base64_protocol_and_default_model(self):
        with patch.dict(os.environ, {}, clear=True):
            result = self.provider().transcribe(self.audio, "Japanese")
        self.assertEqual(result.text, "こんにちは。\n次の行。")
        path, headers, payload = self.requests[0]
        self.assertEqual(path, "/v1/audio/transcriptions")
        self.assertEqual(headers["Authorization"], "Bearer file-secret")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(payload, {"model": "google/gemini-3.5-transcribe",
                                  "language": "ja",
                                  "input_audio": {"data": base64.b64encode(self.audio.read_bytes()).decode(),
                                                  "format": "wav"}})

    def test_environment_precedes_file_and_custom_model_is_sent(self):
        config = ToolConfig("openrouter", "another-asr", self.options)
        with patch.dict(os.environ, VOICE_TEST_OR_KEY="environment-secret"):
            default_registry().create("asr", config).transcribe(self.audio)
        self.assertEqual(self.requests[0][1]["Authorization"], "Bearer environment-secret")
        self.assertEqual(self.requests[0][2]["model"], "another-asr")
        self.assertNotIn("language", self.requests[0][2])

    def test_raw_key_file_is_supported(self):
        self.key.write_text("raw-secret\n")
        self.provider().transcribe(self.audio)
        self.assertEqual(self.requests[0][1]["Authorization"], "Bearer raw-secret")

    @unittest.skipUnless(shutil.which("ffmpeg"), "需要 FFmpeg")
    def test_single_audio_cli_uses_openrouter_key_file_and_json_output(self):
        config = self.root / "asr.json"
        config.write_text(json.dumps({"provider": "openrouter", "options": self.options}))
        result = subprocess.run([sys.executable, "-m", "voice_tools", "asr", str(self.audio),
                                 "--config", str(config), "--model", "cli-model"],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["text"], "こんにちは。\n次の行。")
        self.assertEqual(self.requests[0][2]["model"], "cli-model")
        self.assertNotIn("file-secret", result.stdout + result.stderr)

    def test_missing_key_invalid_wav_and_size_limit_fail_before_network(self):
        self.key.write_text("# no key\n")
        with self.assertRaises(AudioError):
            self.provider().transcribe(self.audio)
        self.key.write_text("raw-secret")
        self.audio.write_bytes(b"RIFF" + b"\0" * 40)
        with self.assertRaises(AudioError):
            self.provider().transcribe(self.audio)
        make_wav(self.audio)
        with self.assertRaises(AudioError):
            self.provider(max_audio_bytes=100).transcribe(self.audio)
        self.assertEqual(self.requests, [])

    def test_upstream_errors_and_bad_results_do_not_expose_response_or_key(self):
        for status, response in [(401, b"file-secret"), (429, b"file-secret"),
                                 (200, b"not JSON file-secret"), (200, {"text": 1}),
                                 (200, {"text": "   "}), (200, {"error": "file-secret"}),
                                 (302, b"file-secret")]:
            with self.subTest(status=status, response=response):
                self.status, self.response = status, response
                with self.assertRaises(AudioError) as error:
                    self.provider().transcribe(self.audio)
                self.assertNotIn("file-secret", str(error.exception))
        self.assertEqual(len(self.requests), 7)


if __name__ == "__main__":
    unittest.main()
