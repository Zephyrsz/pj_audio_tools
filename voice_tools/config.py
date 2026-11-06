"""JSON 配置：功能、后端、模型三个层次独立选择。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .extract import AudioError
from .models import ToolConfig, finite
from .storage import read_json


@dataclass(frozen=True)
class PipelineConfig:
    language: str | None = None
    asr: ToolConfig = field(default_factory=lambda: ToolConfig(model="0.6B"))
    aligner: ToolConfig = field(default_factory=ToolConfig)
    vad: ToolConfig = field(default_factory=ToolConfig)
    llm: ToolConfig | None = None
    tts: ToolConfig | None = None
    window_seconds: float = 24
    context_seconds: float = .6
    boundary_search_seconds: float = 2
    pad_before: float = .1
    pad_after: float = .2
    max_sentence_seconds: float = 30
    skip_silence: bool = True
    asr_group_size: int = 32
    channel: str | int = "mix"

    def __post_init__(self):
        for name in ("asr", "aligner", "vad"):
            if not isinstance(getattr(self, name), ToolConfig):
                raise AudioError(f"{name} 必须为 ToolConfig。")
        for name in ("llm", "tts"):
            if getattr(self, name) is not None and not isinstance(getattr(self, name), ToolConfig):
                raise AudioError(f"{name} 必须为 ToolConfig 或 null。")
        for name in ("window_seconds", "max_sentence_seconds"):
            finite(getattr(self, name), name, minimum=.1)
        for name in ("context_seconds", "boundary_search_seconds", "pad_before", "pad_after"):
            finite(getattr(self, name), name)
        if self.context_seconds >= self.window_seconds / 2:
            raise AudioError("context_seconds 必须小于工作窗口的一半。")
        if self.boundary_search_seconds >= self.window_seconds / 2:
            raise AudioError("boundary_search_seconds 必须小于工作窗口的一半。")
        if self.channel != "mix" and (type(self.channel) is not int or self.channel not in (0, 1)):
            raise AudioError("channel 仅支持 mix、0、1。")
        if type(self.skip_silence) is not bool:
            raise AudioError("skip_silence 必须为布尔值。")
        if type(self.asr_group_size) is not int or not 1 <= self.asr_group_size <= 256:
            raise AudioError("asr_group_size 必须为 1–256 的整数。")
        if self.language is not None and (not isinstance(self.language, str) or not self.language.strip()):
            raise AudioError("language 不能为空字符串。")

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise AudioError("pipeline 配置必须为 JSON 对象。")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise AudioError(f"未知 pipeline 配置：{', '.join(sorted(unknown))}")
        options = dict(data)
        for kind in ("asr", "aligner", "vad", "llm", "tts"):
            if kind in options and options[kind] is not None:
                options[kind] = ToolConfig.from_dict(options[kind])
        return cls(**options)

    @classmethod
    def load(cls, path: str | Path):
        return cls.from_dict(read_json(Path(path)))

    def to_dict(self):
        return asdict(self)
