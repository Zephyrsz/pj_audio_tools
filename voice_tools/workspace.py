"""任务目录生命周期：所有权检查、锁、子目录与 JSONL 阶段日志。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import time

from .extract import AudioError
from .storage import dumps, read_json, write_json


class TaskWorkspace:
    """已加锁的任务目录；只在 open_task 的 with 块内有效。"""

    def __init__(self, root: Path, log: Path, progress=None):
        self.root = root
        self.log = log
        self.progress = progress or (lambda text: None)

    def event(self, stage, status, **details):
        row = {"at": datetime.now(timezone.utc).isoformat(), "stage": stage,
               "status": status, **details}
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(dumps(row) + "\n")

    @contextmanager
    def stage(self, name, **details):
        self.progress(name)
        self.event(name, "started", **details)
        started = time.perf_counter()
        try:
            yield
        except BaseException as exc:
            self.event(name, "failed", elapsed=time.perf_counter() - started,
                       error=str(exc)[:2000] if isinstance(exc, AudioError) else type(exc).__name__)
            raise
        else:
            self.event(name, "completed", elapsed=time.perf_counter() - started)


@contextmanager
def open_task(task: Path, source: Path, identity: dict, *, resume=False, progress=None):
    """检查并锁定任务目录，生成 TaskWorkspace；退出时释放锁。

    新任务要求目录为空；resume 要求 task.json 的 source 与 identity 一致。
    with 块内外的 OSError 均转换为 AudioError。
    """
    if task.is_symlink() or source.is_relative_to(task.resolve()):
        raise AudioError("任务目录不能是软链接或包含原录音。")
    try:
        task.mkdir(parents=True, exist_ok=True)
        marker = task / "task.json"
        if resume:
            saved = read_json(marker)
            if saved.get("generator") != "voice-tools" or saved.get("source") != identity:
                raise AudioError("恢复任务的输入、时间范围或声道与原任务不同。")
        elif any(task.iterdir()):
            raise AudioError("任务目录已存在且非空；恢复请加 --resume，或选择新目录。")
        lock = task / ".lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise AudioError(f"任务正被使用；若进程已停止，可手动移走 {lock} 后恢复。") from exc
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()))
        try:
            if not resume:
                write_json(marker, {"schema_version": 1, "generator": "voice-tools", "source": identity})
            for name in ("work", "cache", "logs"):
                if (task / name).is_symlink():
                    raise AudioError(f"任务子目录不能是软链接：{name}")
                (task / name).mkdir(exist_ok=True)
            log = task / "logs" / "process.jsonl"
            if log.is_symlink():
                raise AudioError("日志不能是软链接。")
            yield TaskWorkspace(task, log, progress)
        finally:
            lock.unlink(missing_ok=True)
    except OSError as exc:
        raise AudioError(f"任务文件处理失败：{exc}") from exc
