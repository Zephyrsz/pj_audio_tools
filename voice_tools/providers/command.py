"""外部程序协议：argv + stdin JSON → stdout JSON，不经过 shell。"""

from __future__ import annotations

import json

from .base import SequentialASR, run, spans_from_json, transcript_from_json, words_from_json
from ..extract import AudioError


class CommandBase:
    kind = ""

    def __init__(self, config):
        self.config = config
        self.argv = config.options.get("argv")
        if (not isinstance(self.argv, list) or not self.argv or
                any(not isinstance(v, str) or not v for v in self.argv)):
            raise AudioError("command 后端需提供非空 options.argv 字符串数组。")

    def request(self, **payload):
        request = {"schema_version": 1, "kind": self.kind, "model": self.config.model, **payload}
        stdout = run(self.argv, stdin=json.dumps(request, ensure_ascii=False),
                     timeout=self.config.options.get("timeout", 1800))
        try:
            result = json.loads(stdout)
        except ValueError as exc:
            raise AudioError("command stdout 必须是单个 JSON 对象；日志请写入 stderr。") from exc
        if not isinstance(result, dict):
            raise AudioError("command 必须返回 JSON 对象。")
        return result


class CommandASR(CommandBase, SequentialASR):
    kind = "asr"

    def transcribe(self, audio, language=None):
        return transcript_from_json(self.request(audio_path=str(audio), language=language))


class CommandAligner(CommandBase):
    kind = "aligner"

    def align(self, audio, text, language=None):
        return words_from_json(self.request(audio_path=str(audio), text=text, language=language).get("words"))


class CommandVAD(CommandBase):
    kind = "vad"

    def detect(self, audio):
        return spans_from_json(self.request(audio_path=str(audio)).get("spans"))


class CommandLLM(CommandBase):
    kind = "llm"

    def complete(self, messages, *, json_mode=False):
        data = self.request(messages=messages, json_mode=json_mode)
        if not isinstance(data.get("text"), str):
            raise AudioError("LLM command 必须返回字符串 text。")
        return data["text"]


class CommandTTS(CommandBase):
    kind = "tts"

    def synthesize(self, text, output, language=None):
        data = self.request(text=text, output_path=str(output), language=language)
        if data.get("ok") is not True:
            raise AudioError("TTS command 必须生成指定音频并返回 {\"ok\": true}。")
