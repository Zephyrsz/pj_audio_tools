"""按功能注册工具；pipeline 不依赖具体模型或厂商。"""

from __future__ import annotations

from typing import Callable

from .extract import AudioError
from .models import ToolConfig

KINDS = ("asr", "aligner", "vad", "llm", "tts")


class Registry:
    def __init__(self):
        self._factories: dict[tuple[str, str], Callable] = {}

    def register(self, kind: str, provider: str, factory: Callable, *, replace=False):
        if kind not in KINDS or not provider or not callable(factory):
            raise AudioError("无效的工具注册。")
        key = (kind, provider)
        if key in self._factories and not replace:
            raise AudioError(f"工具已注册：{kind}/{provider}")
        self._factories[key] = factory
        return self

    def create(self, kind: str, config: ToolConfig):
        factory = self._factories.get((kind, config.provider))
        if factory is None:
            raise AudioError(f"不支持 {kind} 后端 {config.provider!r}；"
                             f"可选：{', '.join(self.available().get(kind, []))}")
        return factory(config)

    def available(self) -> dict[str, list[str]]:
        return {kind: sorted(p for k, p in self._factories if k == kind) for kind in KINDS}


def default_registry() -> Registry:
    from .providers.soniqo import SoniqoASR, SoniqoAligner, SoniqoVAD, SoniqoTTS
    from .providers.compatible import CompatibleASR, CompatibleLLM, CompatibleTTS
    from .providers.command import CommandASR, CommandAligner, CommandVAD, CommandLLM, CommandTTS
    from .providers.energy import EnergyVAD
    from .providers.openrouter_asr import OpenRouterASR
    registry = Registry()
    providers = {
        "asr": {"soniqo": SoniqoASR, "openai-compatible": CompatibleASR,
                "openrouter": OpenRouterASR, "command": CommandASR},
        "aligner": {"soniqo": SoniqoAligner, "command": CommandAligner},
        "vad": {"soniqo": SoniqoVAD, "energy": EnergyVAD, "command": CommandVAD},
        "llm": {"openai-compatible": CompatibleLLM, "command": CommandLLM},
        "tts": {"soniqo": SoniqoTTS, "openai-compatible": CompatibleTTS, "command": CommandTTS},
    }
    for kind, choices in providers.items():
        for name, factory in choices.items():
            registry.register(kind, name, factory)
    return registry
