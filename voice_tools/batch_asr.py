"""目录批量 ASR：时长过滤、逐文件检查点及 datasets_manager 字幕清单。"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .audio import validate_wav
from .extract import AudioError
from .models import ToolConfig, finite
from .providers.openrouter_asr import DEFAULT_MODEL
from .storage import dumps, file_hash, publish, read_json, write_json, write_text
from .tools import transcribe_audio


def scan_directory(input_path, *, min_duration=2, max_duration=None):
    """递归扫描 PCM WAV；下限严格大于，上限（如有）包含边界。不调用模型。"""
    minimum = finite(min_duration, "min_duration")
    maximum = None if max_duration is None else finite(max_duration, "max_duration")
    if maximum is not None and maximum <= minimum:
        raise AudioError("max_duration 必须大于 min_duration。")
    root = Path(input_path).expanduser().resolve()
    if not root.is_dir():
        raise AudioError(f"找不到输入目录：{root}")
    audio_dir = root / "wav" if (root / "wav").is_dir() else root
    files, ids = [], set()
    for path in sorted(audio_dir.rglob("*")):
        if path.suffix.lower() != ".wav" or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            raise AudioError(f"WAV 软链接越出输入目录：{path.name}")
        identifier = path.stem
        if (not identifier or identifier != identifier.strip() or
                any(c in identifier for c in ":\\") or identifier.splitlines() != [identifier] or
                identifier.casefold() in ids):
            raise AudioError(f"文件名重复或不适合字幕清单：{path.name}")
        ids.add(identifier.casefold())
        duration = validate_wav(path)
        selected = duration > minimum and (maximum is None or duration <= maximum)
        files.append({"id": identifier, "path": str(path.relative_to(root)),
                      "duration": duration, "sha256": file_hash(path), "selected": selected})
    if not files:
        raise AudioError("输入目录中没有 WAV 文件。")
    return {"input": str(root), "min_duration": minimum, "max_duration": maximum, "files": files}


def _same_export(path, expected_hash):
    if path.is_symlink():
        raise AudioError(f"拒绝写入软链接：{path}")
    if path.exists():
        if not path.is_file() or file_hash(path) != expected_hash:
            raise AudioError(f"已有导出被修改或与任务不符，拒绝覆盖：{path}")
        return True
    return False


def _transcript_text(files, results):
    return "".join(f"{row['id']}:{results[row['id']]['text']}\n" for row in files)


def _check_resume_exports(output, files, results):
    wav = output / "wav"
    if wav.is_symlink() or (wav.exists() and not wav.is_dir()):
        raise AudioError("输出 wav 必须为真实目录。")
    known = {Path(row["path"]).name: row for row in files}
    if wav.exists():
        for path in wav.iterdir():
            row = known.get(path.name)
            if row is None or row["id"] not in results:
                raise AudioError("输出 wav 含有检查点之外的文件；请使用新目录。")
            _same_export(path, row["sha256"])
    transcript = output / "transcript_utf8.txt"
    if transcript.exists() or transcript.is_symlink():
        if len(results) != len(files):
            raise AudioError("未完成的任务存在额外字幕清单；请使用新目录。")
        expected = hashlib.sha256(_transcript_text(files, results).encode("utf-8")).hexdigest()
        _same_export(transcript, expected)


def transcribe_directory(input_path, output_path, *, config=None, language=None,
                         min_duration=2, max_duration=None, resume=False,
                         registry=None, progress=None):
    """过滤整个 WAV 文件的时长（不是 VAD 讲话时长），复制原音频并重新识别。

    已成功识别的字幕先逐条保存到 batch_asr.json，恢复时不重复调用。
    遇到模型错误立即停止；所有字幕成功后才发布 transcript_utf8.txt。
    """
    request = scan_directory(input_path, min_duration=min_duration, max_duration=max_duration)
    root = Path(request["input"])
    output = Path(output_path).expanduser().absolute()
    if (output.resolve().is_relative_to(root) or root.is_relative_to(output.resolve()) or
            output.is_symlink()):
        raise AudioError("输出必须是与输入相互独立的新目录，且不能是软链接。")
    config = config or ToolConfig("openrouter", DEFAULT_MODEL)
    if config.provider == "openrouter" and config.model is None:
        config = replace(config, model=DEFAULT_MODEL)
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise AudioError("language 必须是非空字符串或 None。")
    request.update(config=config.to_dict(), language=language)
    files = [row for row in request["files"] if row["selected"]]
    if not files:
        raise AudioError("没有 WAV 满足时长条件；未创建输出或调用模型。")
    checkpoint = output / "batch_asr.json"
    lock = output / ".batch_asr.lock"
    locked = False
    try:
        if resume:
            if not output.is_dir():
                raise AudioError("恢复目录不存在；首次运行请去掉 --resume。")
        else:
            output.mkdir(parents=True, exist_ok=False)
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise AudioError("此输出目录有任务锁；确认无其他进程后可移除 .batch_asr.lock 再恢复。") from None
        os.close(descriptor)
        locked = True
        if resume:
            if checkpoint.is_symlink():
                raise AudioError("检查点不能是软链接。")
            state = read_json(checkpoint)
            if (not isinstance(state, dict) or state.get("version") != 1 or
                    state.get("status") not in ("processing", "complete") or
                    state.get("request") != request or not isinstance(state.get("results"), dict)):
                raise AudioError("输入音频、模型、过滤条件或语言与检查点不符；请使用新目录。")
        else:
            state = {"version": 1, "status": "processing", "request": request, "results": {}}
            write_json(checkpoint, state)
        results = state["results"]
        valid_ids = {row["id"] for row in files}
        if any(identifier not in valid_ids or not isinstance(result, dict) or
               not isinstance(result.get("text"), str) or not result["text"].strip() or
               "\n" in result["text"] or "\r" in result["text"]
               for identifier, result in results.items()):
            raise AudioError("检查点字幕结构无效；请使用新目录。")
        _check_resume_exports(output, files, results)
        (output / "wav").mkdir(exist_ok=True)
        for index, row in enumerate(files, 1):
            identifier, source = row["id"], root / row["path"]
            if progress:
                progress(f"{index}/{len(files)} {source.name} ({row['duration']:.3f}s)"
                         + (" [缓存]" if identifier in results else " [ASR]"))
            if identifier not in results:
                if file_hash(source) != row["sha256"]:
                    raise AudioError(f"输入 WAV 在扫描后被修改：{source.name}")
                transcript = transcribe_audio(source, config=config, language=language, registry=registry)
                text = " ".join(transcript.text.split())
                if not text:
                    raise AudioError(f"ASR 返回空字幕：{source.name}")
                results[identifier] = {"text": text, "language": transcript.language}
                write_json(checkpoint, state, overwrite=True)
            destination = output / "wav" / source.name
            if not _same_export(destination, row["sha256"]):
                with tempfile.TemporaryDirectory(prefix=".batch-asr-", dir=output) as directory:
                    temporary = Path(directory) / "audio.wav"
                    shutil.copy2(source, temporary)
                    if file_hash(temporary) != row["sha256"]:
                        raise AudioError(f"复制时发现输入 WAV 被修改：{source.name}")
                    publish(temporary, destination, False)
        text = _transcript_text(files, results)
        transcript_path = output / "transcript_utf8.txt"
        if not _same_export(transcript_path, hashlib.sha256(text.encode("utf-8")).hexdigest()):
            write_text(transcript_path, text)
        if state["status"] != "complete":
            state["status"] = "complete"
            write_json(checkpoint, state, overwrite=True)
    except OSError as error:
        raise AudioError(f"批量 ASR 文件操作失败：{error}") from error
    finally:
        if locked:
            lock.unlink(missing_ok=True)
    return output


def add_arguments(parser):
    parser.add_argument("input", help="WAV 目录，或包含 wav/ 的数据集目录；递归扫描")
    parser.add_argument("-o", "--output", required=True, help="新的输出数据集目录")
    parser.add_argument("--config", help="单个 ASR 工具的 JSON 配置；默认 OpenRouter")
    parser.add_argument("--model", help=f"默认 {DEFAULT_MODEL}")
    parser.add_argument("--api-key-file", help="默认项目 configs/or.cfg；也支持原始密钥文件")
    parser.add_argument("--api-key-env", help="优先读取的密钥环境变量；默认 OPENROUTER_API_KEY")
    parser.add_argument("--timeout", type=float, help="单次 HTTP 请求超时秒数；默认 180")
    parser.add_argument("--language", help="可选语言提示，例如 ja 或 Japanese；默认自动识别")
    parser.add_argument("--min-duration", type=float, default=2, help="只保留严格大于此秒数的 WAV；默认 2")
    parser.add_argument("--max-duration", type=float, help="可选最长秒数，包含该边界")
    parser.add_argument("--resume", action="store_true", help="按检查点恢复，不重复识别成功的 WAV")
    parser.add_argument("--dry-run", action="store_true", help="只检查/统计 WAV，不读取密钥或调用模型")


def handle(args):
    if args.dry_run:
        report = scan_directory(args.input, min_duration=args.min_duration, max_duration=args.max_duration)
        report["selected"] = sum(row["selected"] for row in report["files"])
        report["skipped"] = len(report["files"]) - report["selected"]
        print(dumps(report))
        return
    config = ToolConfig.from_dict(read_json(Path(args.config))) if args.config else ToolConfig("openrouter")
    options = dict(config.options)
    for name in ("api_key_file", "api_key_env", "timeout"):
        value = getattr(args, name)
        if value is not None:
            options[name] = value
    config = ToolConfig(config.provider, args.model or config.model, options)
    result = transcribe_directory(args.input, args.output, config=config, language=args.language,
                                  min_duration=args.min_duration, max_duration=args.max_duration,
                                  resume=args.resume,
                                  progress=lambda message: print(message, file=sys.stderr, flush=True))
    print(f"已保存数据集：{result}")


def main():
    parser = argparse.ArgumentParser(description="批量 ASR，复制 WAV 并生成 transcript_utf8.txt")
    add_arguments(parser)
    args = parser.parse_args()
    try:
        handle(args)
    except AudioError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消，可使用 --resume 恢复。", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
