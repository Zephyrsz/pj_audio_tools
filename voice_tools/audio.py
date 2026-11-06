"""有界 PCM 读写与 FFmpeg 格式转换。"""

from __future__ import annotations

from array import array
import math
from pathlib import Path
import shutil
import sys
import wave

from .extract import AudioError, _duration, _run
from .models import Span


def duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise AudioError("缺少 ffprobe。")
    return _duration(path, ffprobe)


def convert(source: Path, output: Path, *, start=0, end=None, mono=False,
            sample_rate=None, channel="mix") -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioError("缺少 FFmpeg。")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if start:
        command += ["-ss", f"{start:.9f}"]
    command += ["-i", str(source)]
    if end is not None:
        command += ["-t", f"{end - start:.9f}"]
    command += ["-map", "0:a:0", "-vn", "-map_metadata", "-1"]
    if channel != "mix":
        command += ["-af", f"pan=mono|c0=c{channel}"]
    elif mono:
        command += ["-ac", "1"]
    if sample_rate:
        command += ["-ar", str(sample_rate)]
    command += ["-c:a", "pcm_s16le", str(output)]
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(command)
    validate_wav(output)
    return output


def validate_wav(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as source:
            frames = source.getnframes()
            size = source.getnchannels() * source.getsampwidth()
            if not frames:
                raise AudioError("生成了空音频。")
            remaining = frames
            while remaining:
                block = source.readframes(min(remaining, 65536))
                if not block or len(block) % size:
                    raise AudioError("生成了截断的 WAV。")
                remaining -= len(block) // size
            return frames / source.getframerate()
    except (OSError, EOFError, wave.Error) as exc:
        raise AudioError(f"无效的 PCM WAV {path}: {exc}") from exc


def slice_pcm(source: Path, output: Path, start: float, end: float) -> None:
    """分析 WAV 随机访问，避免每个窗口启动解码器。"""
    try:
        with wave.open(str(source), "rb") as reader:
            rate = reader.getframerate()
            begin = round(start * rate)
            finish = min(reader.getnframes(), round(end * rate))
            if finish <= begin:
                raise AudioError("PCM 切片范围为空。")
            reader.setpos(begin)
            with wave.open(str(output), "wb") as writer:
                writer.setparams(reader.getparams())
                remaining = finish - begin
                while remaining:
                    data = reader.readframes(min(remaining, 65536))
                    if not data:
                        raise AudioError("PCM 源文件提前结束。")
                    writer.writeframesraw(data)
                    remaining -= len(data) // (reader.getnchannels() * reader.getsampwidth())
    except (OSError, wave.Error) as exc:
        raise AudioError(f"无法切分 PCM：{exc}") from exc


def energy_frames(path: Path, step=.02):
    try:
        with wave.open(str(path), "rb") as reader:
            if reader.getsampwidth() != 2 or reader.getnchannels() != 1:
                raise AudioError("能量分析需要 PCM16 单声道 WAV。")
            rate = reader.getframerate()
            size, offset = max(1, round(rate * step)), 0
            while True:
                data = reader.readframes(size)
                if not data:
                    break
                samples = array("h")
                samples.frombytes(data)
                if sys.byteorder != "little":
                    samples.byteswap()
                rms = math.sqrt(sum(v * v for v in samples) / len(samples))
                yield offset / rate, (offset + len(samples)) / rate, 20 * math.log10(max(rms, 1e-9) / 32768)
                offset += len(samples)
    except (OSError, wave.Error) as exc:
        raise AudioError(f"无法分析 WAV：{exc}") from exc


def silence_midpoints(spans: list[Span], duration: float, minimum=.12) -> list[float]:
    previous, points = 0.0, []
    for span in [*spans, Span(duration, duration)]:
        if span.start - previous >= minimum:
            points.append((previous + span.start) / 2)
        previous = max(previous, span.end)
    return points
