"""可独立调用的模型工具；pipeline 使用同一个 Registry。"""

from __future__ import annotations

from pathlib import Path
import tempfile

from .audio import convert, validate_wav
from .extract import AudioError
from .models import ToolConfig
from .registry import default_registry
from .storage import publish
from .segmentation import align_units
from .providers.base import spans_from_json
from dataclasses import asdict


def align_audio(input_path, text, *, config=None, language=None, registry=None):
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise AudioError(f"找不到音频：{source}")
    provider = (registry or default_registry()).create("aligner", config or ToolConfig())
    with tempfile.TemporaryDirectory(prefix="voice-tools-align-") as directory:
        audio = convert(source, Path(directory) / "input.wav", mono=True, sample_rate=16000)
        return align_units(text, provider.align(audio, text, language), validate_wav(audio))


def detect_speech(input_path, *, config=None, registry=None):
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise AudioError(f"找不到音频：{source}")
    provider = (registry or default_registry()).create("vad", config or ToolConfig())
    with tempfile.TemporaryDirectory(prefix="voice-tools-vad-") as directory:
        audio = convert(source, Path(directory) / "input.wav", mono=True, sample_rate=16000)
        spans = spans_from_json([asdict(s) for s in provider.detect(audio)])
        total = validate_wav(audio)
        if any(s.end > total + .032 for s in spans):
            raise AudioError("VAD 区间超出音频。")
        return spans


def transcribe_audio(input_path, *, config=None, language=None, registry=None):
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise AudioError(f"找不到音频：{source}")
    provider = (registry or default_registry()).create("asr", config or ToolConfig())
    with tempfile.TemporaryDirectory(prefix="voice-tools-input-") as directory:
        audio = convert(source, Path(directory) / "input.wav", mono=True, sample_rate=16000)
        return provider.transcribe(audio, language)


def complete_text(text, *, config, registry=None, system=None):
    if not isinstance(text, str) or not text.strip():
        raise AudioError("LLM 输入文字不能为空。")
    messages = ([{"role": "system", "content": system}] if system else [])
    messages.append({"role": "user", "content": text})
    return (registry or default_registry()).create("llm", config).complete(messages)


def synthesize_text(text, output_path, *, config=None, language=None,
                    overwrite=False, registry=None):
    if not isinstance(text, str) or not text.strip():
        raise AudioError("TTS 输入文字不能为空。")
    output = Path(output_path).expanduser().absolute()
    if output.suffix.lower() != ".wav":
        raise AudioError("TTS 输出必须为 WAV。")
    if output.is_symlink() or (output.exists() and not overwrite):
        raise AudioError(f"输出已存在或是软链接：{output}")
    reference = (config or ToolConfig()).options.get("voice_sample")
    if reference:
        path = Path(reference).expanduser().resolve()
        if output.resolve() == path or (output.exists() and path.exists() and output.samefile(path)):
            raise AudioError("TTS 输出不能覆盖参考音频。")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".voice-tools-tts-", dir=output.parent) as directory:
            raw, ready = Path(directory) / "raw.wav", Path(directory) / "ready.wav"
            provider = (registry or default_registry()).create("tts", config or ToolConfig())
            provider.synthesize(text, raw, language)
            if not raw.is_file():
                raise AudioError("TTS 未生成音频。")
            convert(raw, ready)
            publish(ready, output, overwrite)
    except OSError as exc:
        raise AudioError(f"TTS 输出失败：{exc}") from exc
    return output
