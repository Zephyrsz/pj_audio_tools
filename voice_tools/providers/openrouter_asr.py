"""从 tts_test 后台提取的 OpenRouter JSON/Base64 ASR，独立于 Web 服务。"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from .base import SequentialASR, transcript_from_json
from .compatible import CompatibleBase
from ..audio import validate_wav
from ..extract import AudioError
from ..languages import language_code
from ..models import ToolConfig

DEFAULT_MODEL = "google/gemini-3.5-transcribe"
DEFAULT_KEY_FILE = Path(__file__).resolve().parents[2] / "configs" / "or.cfg"
MAX_AUDIO_BYTES = 15 * 1024 * 1024


class OpenRouterASR(CompatibleBase, SequentialASR):
    """识别 PCM WAV；密钥优先从环境变量读取，再从本地 cfg 文件读取。"""

    def __init__(self, config):
        options = {"base_url": "https://openrouter.ai/api/v1",
                   "api_key_env": "OPENROUTER_API_KEY", "timeout": 180,
                   **config.options}
        super().__init__(ToolConfig(config.provider, config.model or DEFAULT_MODEL, options))
        self.max_audio_bytes = options.get("max_audio_bytes", MAX_AUDIO_BYTES)
        if type(self.max_audio_bytes) is not int or not 44 <= self.max_audio_bytes <= MAX_AUDIO_BYTES:
            raise AudioError("max_audio_bytes 必须为 44–15728640 的整数（最多 15 MiB）。")

    def _api_key(self):
        variable = self.options["api_key_env"]
        if not isinstance(variable, str) or not variable.strip():
            raise AudioError("OpenRouter api_key_env 必须是环境变量名称。")
        key = os.environ.get(variable, "").strip()
        if not key:
            filename = self.options.get("api_key_file", str(DEFAULT_KEY_FILE))
            if not isinstance(filename, str) or not filename.strip():
                raise AudioError("api_key_file 必须是配置文件路径。")
            try:
                lines = [line.strip() for line in Path(filename).expanduser().read_text(
                    encoding="utf-8-sig").splitlines()
                         if line.strip() and not line.lstrip().startswith(("#", ";"))]
            except (OSError, UnicodeError):
                raise AudioError(f"无法读取 OpenRouter 密钥文件；请配置 {variable} 或 api_key_file。") from None
            if len(lines) == 1 and "=" not in lines[0]:
                key = lines[0]
            else:
                for line in lines:
                    name, separator, value = line.removeprefix("export ").partition("=")
                    if separator and name.strip() in {variable, "OPENROUTER_API_KEY"}:
                        key = value.strip().strip("\"'")
                        break
        if not key or any(character.isspace() or ord(character) < 32 for character in key):
            raise AudioError("未找到有效的 OpenRouter API Key；支持原始密钥或 OPENROUTER_API_KEY=...。")
        return key

    def transcribe(self, audio: Path, language=None):
        audio = Path(audio)
        try:
            if not 44 <= audio.stat().st_size <= self.max_audio_bytes:
                raise AudioError("OpenRouter ASR 的 WAV 必须非空且不超过 15 MiB。")
            validate_wav(audio)
            content = audio.read_bytes()
        except OSError:
            raise AudioError("无法读取 OpenRouter ASR 输入 WAV。") from None
        payload = {"model": self.config.model,
                   "input_audio": {"data": base64.b64encode(content).decode("ascii"), "format": "wav"}}
        if language:
            payload["language"] = language_code(language)
        result = transcript_from_json(self.json_request("/audio/transcriptions", payload))
        if not result.text.strip():
            raise AudioError("OpenRouter ASR 返回了空字幕。")
        return result
