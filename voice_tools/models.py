"""工具和 pipeline 之间的模型无关数据结构。时间单位为秒。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from typing import Any, Protocol
from pathlib import Path

from .extract import AudioError


def finite(value: Any, name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AudioError(f"{name} 必须是数值。")
    try:
        result = float(value)
    except OverflowError as exc:
        raise AudioError(f"{name} 超出范围。") from exc
    if not math.isfinite(result) or result < minimum:
        raise AudioError(f"{name} 必须是 >= {minimum:g} 的有限数值。")
    return result


@dataclass(frozen=True)
class Span:
    start: float
    end: float


@dataclass(frozen=True)
class TimedWord:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None = None


@dataclass(frozen=True)
class Unit:
    id: int
    text: str
    start: float
    end: float
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Sentence:
    id: str
    text: str
    first_unit: int
    last_unit: int
    speech_start: float
    speech_end: float
    cut_start: float
    cut_end: float
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolConfig:
    provider: str = "soniqo"
    model: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise AudioError("provider 不能为空。")
        if self.model is not None and (not isinstance(self.model, str) or not self.model.strip()):
            raise AudioError("model 不能为空。")
        if not isinstance(self.options, dict):
            raise AudioError("工具 options 必须为对象。")
        try:
            json.dumps(self.options, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise AudioError("工具 options 必须是有限数值组成的 JSON 可序列化对象。") from exc
        forbidden = {"api_key", "authorization", "token", "password"}
        if any(str(k).lower() in forbidden for k in self.options):
            raise AudioError("请使用 api_key_env 引用环境变量，不要在配置中写入凭据。")

    @classmethod
    def from_dict(cls, data: dict) -> ToolConfig:
        if not isinstance(data, dict) or set(data) - {"provider", "model", "options"}:
            raise AudioError("工具配置只能包含 provider、model、options。")
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)


class ASR(Protocol):
    def transcribe(self, audio: Path, language: str | None = None) -> Transcript: ...
    def transcribe_many(self, audio: list[Path], language: str | None = None) -> list[Transcript]: ...


class Aligner(Protocol):
    def align(self, audio: Path, text: str, language: str | None = None) -> list[TimedWord]: ...


class VAD(Protocol):
    def detect(self, audio: Path) -> list[Span]: ...


class LLM(Protocol):
    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str: ...


class TTS(Protocol):
    def synthesize(self, text: str, output: Path, language: str | None = None) -> None: ...
