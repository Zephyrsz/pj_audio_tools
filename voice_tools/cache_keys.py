"""阶段缓存键：工具配置、适配器类、可执行文件或参考音频变化时缓存失效。"""

from __future__ import annotations

from pathlib import Path
import shutil

from .extract import AudioError
from .models import ToolConfig
from .storage import file_hash

PIPELINE_VERSION = 1


def provider_key(kind: str, spec: ToolConfig, provider) -> dict:
    key = {"config": spec.to_dict(), "pipeline_version": PIPELINE_VERSION,
           "adapter": type(provider).__module__ + "." + type(provider).__qualname__}
    # A replaced CLI executable or command script invalidates its cached results.
    argv = spec.options.get("argv", [spec.options.get("executable", "speech")])
    files = []
    for item in argv:
        path = shutil.which(item) if isinstance(item, str) else None
        if path is None and isinstance(item, str) and Path(item).is_file():
            path = item
        if path:
            stat = Path(path).stat()
            files.append([str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns])
    key["tools"] = files
    if kind == "tts" and spec.options.get("voice_sample"):
        reference = spec.options["voice_sample"]
        if not isinstance(reference, str):
            raise AudioError("voice_sample 必须为参考音频路径。")
        path = Path(reference).expanduser().resolve()
        key["reference_audio"] = {"path": str(path), "sha256": file_hash(path)}
    return key
