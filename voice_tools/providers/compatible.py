"""兼容 HTTP API；仅使用标准库。密钥从环境变量读取，禁止跨源重定向。"""

from __future__ import annotations

import json
import http.client
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .base import SequentialASR, transcript_from_json
from ..extract import AudioError
from ..models import finite
from ..languages import language_code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AudioError("API 返回重定向；请配置最终 base_url。")


class CompatibleBase:
    def __init__(self, config):
        self.config = config
        self.options = config.options
        url = self.options.get("base_url", "https://api.openai.com/v1")
        if not isinstance(url, str):
            raise AudioError("base_url 必须是字符串。")
        self.url = url.rstrip("/")
        parsed = urllib.parse.urlparse(self.url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise AudioError("base_url 必须是无凭据、查询参数和片段的 HTTP(S) 地址。")
        if not config.model:
            raise AudioError("HTTP 后端必须明确指定 model。")
        self.timeout = finite(self.options.get("timeout", 300), "timeout", minimum=.1)

    def _api_key(self):
        variable = self.options.get("api_key_env", "OPENAI_API_KEY")
        if variable is not None and (not isinstance(variable, str) or not variable.strip()):
            raise AudioError("api_key_env 必须为环境变量名称或 null。")
        if variable:
            key = os.environ.get(variable)
            if not key:
                raise AudioError(f"缺少 API 密钥环境变量：{variable}；本地免密服务可设 api_key_env 为 null。")
            return key
        return None

    def request(self, endpoint, data, content_type="application/json") -> bytes:
        headers = {"Content-Type": content_type}
        key = self._api_key()
        if key:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(self.url + endpoint, data=data, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                payload = response.read(64 * 1024 * 1024 + 1)
                if len(payload) > 64 * 1024 * 1024:
                    raise AudioError("API 响应超过 64 MiB 限制。")
                return payload
        except urllib.error.HTTPError as exc:
            # Do not echo server bodies which may contain credentials or prompt data.
            exc.close()
            raise AudioError(f"API 请求失败：HTTP {exc.code} ({endpoint})") from exc
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            raise AudioError(f"API 连接失败或超时：{endpoint}") from exc

    def json_request(self, endpoint, payload):
        raw = self.request(endpoint, json.dumps(payload, ensure_ascii=False).encode())
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise AudioError("API 未返回合法 JSON。") from exc


class CompatibleASR(CompatibleBase, SequentialASR):
    def transcribe(self, audio: Path, language=None):
        boundary = "voice-tools-" + uuid.uuid4().hex
        fields = {"model": self.config.model, "response_format": "json"}
        if language:
            fields["language"] = language_code(language)
        if self.options.get("prompt"):
            fields["prompt"] = self.options["prompt"]
        data = bytearray()
        for key, value in fields.items():
            data += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                     f"{value}\r\n").encode()
        data += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                 'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
        data += audio.read_bytes()
        data += f"\r\n--{boundary}--\r\n".encode()
        raw = self.request("/audio/transcriptions", bytes(data), "multipart/form-data; boundary=" + boundary)
        try:
            return transcript_from_json(json.loads(raw))
        except (ValueError, UnicodeError) as exc:
            raise AudioError("ASR API 未返回合法 JSON。") from exc


class CompatibleLLM(CompatibleBase):
    def complete(self, messages, *, json_mode=False):
        payload = {"model": self.config.model, "messages": messages, "stream": False}
        if "temperature" in self.options:
            payload["temperature"] = self.options["temperature"]
        if json_mode and self.options.get("json_mode", True):
            payload["response_format"] = {"type": "json_object"}
        if "max_completion_tokens" in self.options:
            payload["max_completion_tokens"] = self.options["max_completion_tokens"]
        elif "max_tokens" in self.options:
            payload["max_tokens"] = self.options["max_tokens"]
        data = self.json_request("/chat/completions", payload)
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") not in {"stop", None} or not isinstance(content, str):
                raise AudioError("LLM 输出被截断、拒绝或不是文本。")
            return content
        except (KeyError, IndexError, TypeError) as exc:
            raise AudioError("LLM API 响应缺少 choices/message/content。") from exc


class CompatibleTTS(CompatibleBase):
    def synthesize(self, text, output, language=None):
        payload = {"model": self.config.model, "input": text,
                   "voice": self.options.get("voice", "alloy"), "response_format": "wav"}
        for key in ("instructions", "speed"):
            if key in self.options:
                payload[key] = self.options[key]
        output.write_bytes(self.request("/audio/speech",
                                       json.dumps(payload, ensure_ascii=False).encode()))
