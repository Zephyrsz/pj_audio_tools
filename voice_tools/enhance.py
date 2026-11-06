"""通过本机 Soniqo speech CLI 做语音降噪和修复。"""

from __future__ import annotations

import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

from .extract import AudioError


def _validate_paths(input_path: str | Path, output_path: str | Path | None,
                   suffix: str, overwrite: bool) -> tuple[Path, Path]:
    try:
        source = Path(input_path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise AudioError(f"无法读取输入路径：{exc}") from exc
    if not source.is_file():
        raise AudioError(f"找不到音频文件：{source}")
    if output_path is None:
        output_path = Path("outputs") / f"{source.stem}_{suffix}.wav"
    output = Path(output_path).expanduser().absolute()
    if output.suffix.lower() != ".wav":
        raise AudioError("语音增强输出格式仅支持 .wav。")
    try:
        if output.resolve() == source or (output.exists() and output.samefile(source)):
            raise AudioError("输出文件不能与原音频相同。")
    except (OSError, RuntimeError) as exc:
        raise AudioError(f"无法检查输出路径：{exc}") from exc
    if os.path.lexists(output) and not overwrite:
        raise AudioError(f"输出文件已存在：{output}；如需覆盖请使用 --overwrite。")
    if output.is_dir():
        raise AudioError(f"输出路径是目录：{output}")
    return source, output


def _validate_audio(path: Path) -> None:
    """流式检查完整 PCM WAV，防止发布空文件或截断的模型输出。"""
    try:
        with wave.open(str(path), "rb") as audio:
            remaining = audio.getnframes()
            frame_size = audio.getsampwidth() * audio.getnchannels()
            if remaining <= 0:
                raise AudioError("语音增强失败：speech 生成了空音频。")
            while remaining:
                block = audio.readframes(min(remaining, 65536))
                if not block or len(block) % frame_size:
                    raise AudioError("语音增强失败：speech 生成了不完整的 WAV。")
                remaining -= len(block) // frame_size
    except (OSError, EOFError, wave.Error) as exc:
        raise AudioError(f"语音增强失败：speech 未生成有效的 PCM WAV：{exc}") from exc


def _run_speech(command: list[str], output: Path, overwrite: bool) -> Path:
    temporary = None
    processing_failed = False
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".voice-tools-enhance-", suffix=".wav",
                                              dir=output.parent)
        temporary = Path(temporary_name)
        os.close(fd)
        command = [*command[:3], "-o", str(temporary), *command[3:]]
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    errors="replace", check=False)
        except OSError as exc:
            raise AudioError(f"无法运行 speech：{exc}") from exc
        if result.returncode:
            detail = (result.stderr.strip() or result.stdout.strip())[-2000:]
            raise AudioError(f"语音增强失败：{detail or 'speech 返回了错误。'}")
        _validate_audio(temporary)
        try:
            if overwrite:
                os.replace(temporary, output)
            else:
                os.link(temporary, output)
        except FileExistsError as exc:
            raise AudioError(f"输出文件已存在：{output}；如需覆盖请使用 --overwrite。") from exc
    except OSError as exc:
        processing_failed = True
        raise AudioError(f"无法写入输出文件：{exc}") from exc
    except BaseException:
        processing_failed = True
        raise
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                # Preserve an existing processing error or KeyboardInterrupt.
                if not processing_failed:
                    raise AudioError(
                        f"输出已保存至 {output}，但无法清理临时文件 {temporary}：{exc}"
                    ) from exc
    return output


def _speech_executable(executable: str) -> str:
    resolved = shutil.which(executable)
    if not resolved:
        raise AudioError("缺少 Soniqo speech 命令。请安装 speech-swift 或 Homebrew speech。")
    return resolved


def _validate_model(model: str | None) -> None:
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise AudioError("模型名称不能为空。")


def denoise_audio(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    overwrite: bool = False,
    executable: str = "speech",
    model: str | None = None,
    chunk_seconds: float | None = None,
    overlap_ms: int | None = None,
    no_chunk: bool = False,
) -> Path:
    """使用 DeepFilterNet3 降噪，默认写入 outputs/<stem>_denoised.wav。

    返回绝对路径，失败抛出 AudioError；不自动再经过 Sidon。
    模型默认参数由本机 speech 决定；当前默认输出 48 kHz 单声道 PCM WAV。
    """
    _validate_model(model)
    if chunk_seconds is not None:
        try:
            valid_chunk = (not isinstance(chunk_seconds, bool)
                           and isinstance(chunk_seconds, (int, float))
                           and math.isfinite(chunk_seconds) and chunk_seconds > 0)
        except OverflowError:
            valid_chunk = False
        if not valid_chunk:
            raise AudioError("分块秒数必须是正的有限数值。")
    if overlap_ms is not None and (isinstance(overlap_ms, bool)
                                  or not isinstance(overlap_ms, int) or overlap_ms < 0):
        raise AudioError("重叠毫秒数必须是非负整数。")
    if not isinstance(no_chunk, bool):
        raise AudioError("no_chunk 必须为布尔值。")
    source, output = _validate_paths(input_path, output_path, "denoised", overwrite)
    command = [_speech_executable(executable), "denoise", str(source)]
    if model is not None:
        command.extend(["--model", model])
    if chunk_seconds is not None:
        command.extend(["--chunk-seconds", str(chunk_seconds)])
    if overlap_ms is not None:
        command.extend(["--overlap-ms", str(overlap_ms)])
    if no_chunk:
        command.append("--no-chunk")
    return _run_speech(command, output, overwrite)


def restore_audio(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    overwrite: bool = False,
    executable: str = "speech",
    model: str | None = None,
    variant: str | None = None,
) -> Path:
    """使用 Sidon 修复，默认写入 outputs/<stem>_restored.wav。

    返回绝对路径，失败抛出 AudioError；建议独立处理原始录音进行对比。
    Sidon 会重建语音，可能改变细节；当前默认输出 48 kHz 单声道 PCM WAV。
    """
    _validate_model(model)
    if variant is not None and variant not in ("fp16", "int8"):
        raise AudioError("Sidon 精度仅支持 fp16 或 int8。")
    source, output = _validate_paths(input_path, output_path, "restored", overwrite)
    command = [_speech_executable(executable), "restore", str(source)]
    if model is not None:
        command.extend(["--model", model])
    if variant is not None:
        command.extend(["--variant", variant])
    return _run_speech(command, output, overwrite)
