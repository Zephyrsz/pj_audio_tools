"""单音频切句 pipeline 入口：校验输入、组装依赖、打开任务目录并启动任务流。

阶段编排在 sentence_flow，时间规则在 timeline，任务目录与日志在 workspace。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import uuid

from .audio import duration
from .cache import StageCache
from .cache_keys import PIPELINE_VERSION  # noqa: F401  兼容旧导入
from .config import PipelineConfig
from .extract import AudioError, parse_time
from .registry import default_registry
from .sentence_flow import FlowContext, run_sentence_flow
from .storage import file_hash
from .timeline import merge_units, plan_windows, refine_edges  # noqa: F401  兼容旧导入
from .workspace import open_task


class SentencePipeline:
    def __init__(self, config: PipelineConfig | None = None, *, registry=None, progress=None):
        self.config = config or PipelineConfig()
        self.registry = registry or default_registry()
        self.progress = progress or (lambda text: None)

    def run(self, input_path, output_dir=None, *, start=0, end=None, resume=False) -> Path:
        source = Path(input_path).expanduser().resolve()
        if not source.is_file():
            raise AudioError(f"找不到原录音：{source}")
        config = self.config
        if config.aligner.provider == "soniqo" and not config.language:
            raise AudioError("请通过 --language 或配置文件明确录音语言，例如 Japanese。")
        # 先实例化全部后端，未知工具在创建任务目录前失败。
        providers = {kind: self.registry.create(kind, spec)
                     for kind in ("vad", "asr", "aligner", "llm", "tts")
                     if (spec := getattr(config, kind)) is not None}
        total = duration(source)
        begin, finish = parse_time(start), total if end is None else parse_time(end)
        if not 0 <= begin < finish <= total:
            raise AudioError("处理范围必须满足 0 <= start < end <= 原录音时长。")
        identity = {"path": str(source), "sha256": file_hash(source), "bytes": source.stat().st_size,
                    "source_duration": total, "start": begin, "end": finish, "channel": config.channel}
        if output_dir is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            output_dir = Path("outputs") / f"{source.stem}_sentences_{stamp}_{uuid.uuid4().hex[:6]}"
        task = Path(output_dir).expanduser().absolute()
        with open_task(task, source, identity, resume=resume, progress=self.progress) as workspace:
            try:
                workspace.event("pipeline", "started", config=config.to_dict(), source=identity)
                result = run_sentence_flow(FlowContext(
                    config=config, registry=self.registry, providers=providers,
                    workspace=workspace, source=source, identity=identity,
                    cache=StageCache(task / "cache")))
                workspace.event("pipeline", "completed", dataset=str(result))
                return result
            except BaseException as exc:
                workspace.event("pipeline", "failed", error=str(exc)[:2000])
                raise


def split_sentences(input_path, output_dir=None, *, config=None, registry=None,
                    start=0, end=None, resume=False, progress=None):
    return SentencePipeline(config, registry=registry, progress=progress).run(
        input_path, output_dir, start=start, end=end, resume=resume)
