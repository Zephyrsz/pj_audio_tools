from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from ..extract import AudioError
from ..models import Span, TimedWord, Transcript, finite


def run(argv: list[str], *, stdin: str | None = None, timeout=1800) -> str:
    executable = shutil.which(argv[0])
    if not executable:
        raise AudioError(f"找不到工具：{argv[0]}")
    try:
        completed = subprocess.run([executable, *argv[1:]], input=stdin, capture_output=True,
                                   text=True, errors="replace", timeout=finite(timeout, "timeout", minimum=.1))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioError(f"工具执行失败或超时：{Path(argv[0]).name}") from exc
    if completed.returncode:
        detail = (completed.stderr.strip() or completed.stdout.strip())[-2000:]
        raise AudioError(f"{Path(argv[0]).name} 返回 {completed.returncode}：{detail}")
    return completed.stdout


def spans_from_json(data) -> list[Span]:
    if not isinstance(data, list):
        raise AudioError("VAD 必须返回区间数组。")
    result, previous = [], 0.0
    for row in data:
        if not isinstance(row, dict):
            raise AudioError("VAD 区间必须是对象。")
        start, end = finite(row.get("start"), "start"), finite(row.get("end"), "end")
        if end <= start or start < previous:
            raise AudioError("VAD 区间倒序、重叠或零长度。")
        result.append(Span(start, end))
        previous = end
    return result


def words_from_json(data) -> list[TimedWord]:
    if not isinstance(data, list):
        raise AudioError("对齐必须返回 words 数组。")
    result = []
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise AudioError("无效的对齐词。")
        result.append(TimedWord(row["text"], finite(row.get("start"), "start"),
                                finite(row.get("end"), "end")))
    return result


def transcript_from_json(data) -> Transcript:
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise AudioError("ASR 必须返回包含字符串 text 的 JSON 对象。")
    return Transcript(data["text"], data.get("language"))


def logged_json(stdout: str, expected: type):
    """从日志混合输出中寻找一个完整顶层 JSON，不将任意日志行当有效结果。"""
    decoder = json.JSONDecoder()
    for match in __import__("re").finditer(r"(?m)^[ \t]*(?=[\[{])", stdout):
        try:
            data, _ = decoder.raw_decode(stdout[match.end():])
        except ValueError:
            continue
        if isinstance(data, expected):
            return data
    raise AudioError("工具未返回可识别的结构化结果。")


class SequentialASR:
    def transcribe_many(self, audio: list[Path], language=None):
        return [self.transcribe(path, language) for path in audio]
