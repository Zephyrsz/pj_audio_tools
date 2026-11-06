"""通过 FFmpeg 按时间范围精确截取音频，无第三方 Python 依赖。"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


class AudioError(ValueError):
    """音频输入、参数或处理失败，可直接向用户展示。"""


def parse_time(value: str | int | float) -> float:
    """解析秒数、MM:SS 或 HH:MM:SS，支持小数秒。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise AudioError("时间必须是秒数或 MM:SS / HH:MM:SS 格式。")
    if isinstance(value, (int, float)):
        try:
            seconds = float(value)
        except OverflowError as exc:
            raise AudioError("时间数值过大。") from exc
        if not math.isfinite(seconds) or seconds < 0:
            raise AudioError("时间必须是非负有限数值。")
        return seconds
    text = str(value).strip()
    if not re.fullmatch(r"\d+(?::\d+){0,2}(?:\.\d+)?", text):
        raise AudioError(f"无效时间：{value!r}；请使用非负秒数或 MM:SS / HH:MM:SS。")
    try:
        parts = [float(part) for part in text.split(":")]
        if any(part >= 60 for part in parts[1:]):
            raise AudioError(f"无效时间：{value!r}；冒号后的分、秒必须小于 60。")
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
        if not math.isfinite(seconds):
            raise AudioError("时间必须是有限数值。")
        return seconds
    except OverflowError as exc:
        raise AudioError("时间数值过大。") from exc


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise AudioError(f"无法运行 {Path(command[0]).name}：{exc}") from exc
    if result.returncode:
        detail = result.stderr.strip()[-2000:]
        raise AudioError(f"音频处理失败：{detail or '请检查输入文件和输出路径。'}")
    return result.stdout


def _duration(source: Path, ffprobe: str) -> float:
    raw = _run([
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=duration:format=duration", "-of", "json", str(source),
    ])
    try:
        data = json.loads(raw)
        streams = data.get("streams", [])
        if not streams:
            raise AudioError("输入文件中没有音频轨道。")
        value = streams[0].get("duration")
        if value in (None, "N/A"):
            value = data.get("format", {}).get("duration")
        duration = float(value)
        if not math.isfinite(duration) or duration <= 0:
            raise AudioError("无法读取有效的音频时长。")
        return duration
    except (TypeError, ValueError, KeyError) as exc:
        if isinstance(exc, AudioError):
            raise
        raise AudioError("无法读取有效的音频时长。") from exc


_ENCODERS = {
    ".wav": ["-c:a", "pcm_s16le"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    ".flac": ["-c:a", "flac"],
}


def extract_clip(
    input_path: str | Path,
    start: str | int | float,
    end: str | int | float,
    output_path: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """提取 [start, end) 区间，返回输出绝对路径，失败抛出 AudioError。

    默认保存到当前目录下 outputs/<名称>_<开始秒数>-<结束秒数>.wav。
    保留源采样率和声道；始终重新编码以实现精确定位。
    """
    begin, finish = parse_time(start), parse_time(end)
    if finish <= begin:
        raise AudioError("结束时间必须大于开始时间。")
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise AudioError(f"找不到音频文件：{source}")
    if output_path is None:
        output_path = Path("outputs") / f"{source.stem}_{begin:g}-{finish:g}.wav"
    output = Path(output_path).expanduser().absolute()
    codec = _ENCODERS.get(output.suffix.lower())
    if codec is None:
        raise AudioError("输出格式仅支持 .wav、.m4a、.mp3、.flac。")
    try:
        if output.resolve() == source or (output.exists() and output.samefile(source)):
            raise AudioError("输出文件不能与原音频相同。")
        if os.path.lexists(output) and not overwrite:
            raise AudioError(f"输出文件已存在：{output}；如需覆盖请使用 --overwrite。")
        if output.is_dir():
            raise AudioError(f"输出路径是目录：{output}")
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise AudioError("缺少 FFmpeg/ffprobe。macOS 可运行 brew install ffmpeg 安装。")
        duration = _duration(source, ffprobe)
        if finish > duration:
            raise AudioError(f"结束时间 {finish:g} 秒超出音频时长 {duration:.6f} 秒。")
        output.parent.mkdir(parents=True, exist_ok=True)
        # Same-directory temporary output keeps failures from damaging an existing file.
        fd, temporary_name = tempfile.mkstemp(prefix=".voice-tools-", suffix=output.suffix.lower(),
                                               dir=output.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            _run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-ss", f"{begin:.9f}", "-i", str(source), "-t", f"{finish - begin:.9f}",
                "-map", "0:a:0", "-vn", "-map_metadata", "-1",
                *codec, str(temporary),
            ])
            if overwrite:
                os.replace(temporary, output)
            else:
                # Atomic no-clobber publish, including when another process races us.
                os.link(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        raise AudioError(f"无法写入输出文件：{exc}") from exc
    return output
