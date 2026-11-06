"""原音频切片、全局/局部字幕及 datasets_manager 兼容清单。"""

from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .audio import duration, validate_wav
from .extract import AudioError, extract_clip
from .storage import digest, file_hash, read_json, write_json, write_text
from .tools import synthesize_text


def srt_time(seconds: float) -> str:
    value = max(0, round(seconds * 1000))
    hours, rest = divmod(value, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def srt_cue(index, start, end, text):
    return f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n\n"


def export_dataset(source: Path, task: Path, sentences, identity, config, registry,
                   *, synthesize=None, issues=None, tts_identity=None):
    """完整 staging 成功后才替换本任务旧数据集，失败保留旧结果。"""
    target = task / "dataset"
    issues = issues or []
    dependencies = {"source": identity, "config": config.to_dict(), "issues": issues,
                    "sentences": [asdict(s) for s in sentences], "export_version": 1}
    if config.tts:
        dependencies["tts_provider"] = tts_identity
    export_key = digest(dependencies)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise AudioError("dataset 不能是软链接或普通文件。")
        old = read_json(target / "manifest.json")
        if old.get("generator") != "voice-tools" or old.get("source") != identity:
            raise AudioError("现有 dataset 不属于这个任务，拒绝替换。")
        if "artifacts" in old:
            entries = list(target.rglob("*"))
            actual_files = {p.relative_to(target).as_posix() for p in entries if p.is_file()}
            if (any(p.is_symlink() for p in entries) or
                    actual_files != set(old["artifacts"]) | {"manifest.json"} or
                    any(file_hash(target / relative) != sha for relative, sha in old["artifacts"].items())):
                raise AudioError("数据集已有修改或文件缺失，已保留原结果；请使用新任务目录。")
            if old.get("export_key") == export_key:
                return target
    staging = Path(tempfile.mkdtemp(prefix=".dataset-", dir=task))
    backup = None
    try:
        for subdir in ("wav", "txt", "srt"):
            (staging / subdir).mkdir()
        if config.tts:
            (staging / "tts").mkdir()
        rows, transcript, global_srt = [], [], []
        for index, sentence in enumerate(sentences, 1):
            stem = sentence.id
            audio = staging / "wav" / f"{stem}.wav"
            extract_clip(source, sentence.cut_start, sentence.cut_end, audio)
            actual = validate_wav(audio)
            if abs(actual - (sentence.cut_end - sentence.cut_start)) > .002:
                raise AudioError(f"片段时长不一致：{stem}")
            text = " ".join(sentence.text.split())
            if not text:
                raise AudioError("不允许导出空字幕。")
            transcript.append(f"{stem}:{text}\n")
            write_text(staging / "txt" / f"{stem}.txt", text + "\n")
            relative_start = max(0, sentence.speech_start - sentence.cut_start)
            relative_end = min(actual, sentence.speech_end - sentence.cut_start)
            if relative_end <= relative_start:
                raise AudioError("片段字幕时间范围为空。")
            write_text(staging / "srt" / f"{stem}.srt",
                       srt_cue(1, relative_start, relative_end, text))
            global_srt.append(srt_cue(index, sentence.speech_start, sentence.speech_end, text))
            row = {**asdict(sentence), "audio": f"wav/{stem}.wav",
                   "subtitle": f"srt/{stem}.srt", "duration": actual,
                   "sha256": file_hash(audio)}
            if config.tts:
                generated = staging / "tts" / f"{stem}.wav"
                if synthesize:
                    synthesize(text, generated)
                else:
                    synthesize_text(text, generated, config=config.tts,
                                    language=config.language, registry=registry)
                generated_duration = duration(generated)
                write_text(staging / "tts" / f"{stem}.srt",
                           srt_cue(1, 0, generated_duration, text))
                row["synthetic_audio"] = f"tts/{stem}.wav"
                row["synthetic_duration"] = generated_duration
            rows.append(row)
        expected = {s.id for s in sentences}
        if expected != {p.stem for p in (staging / "wav").glob("*.wav")}:
            raise AudioError("音频与字幕清单没有一一对应。")
        write_text(staging / "transcript_utf8.txt", "".join(transcript))
        write_text(staging / "subtitles.srt", "".join(global_srt))
        manifest = {"schema_version": 1, "generator": "voice-tools",
                    "source": identity, "config": config.to_dict(),
                    "count": len(rows), "review_count": sum(bool(r["flags"]) for r in rows),
                    "sentences": rows, "unassigned_tail_count": len(issues)}
        write_json(staging / "review.json", [r for r in rows if r["flags"]])
        write_json(staging / "alignment_issues.json", issues)
        manifest["export_key"] = export_key
        manifest["artifacts"] = {p.relative_to(staging).as_posix(): file_hash(p)
                                 for p in staging.rglob("*") if p.is_file()}
        write_json(staging / "manifest.json", manifest)
        if file_hash(source) != identity["sha256"]:
            raise AudioError("处理期间原录音发生变化，拒绝发布混合来源的数据集。")
        if target.exists():
            backup = task / (".previous-dataset-" + uuid.uuid4().hex)
            os.rename(target, backup)
        try:
            os.rename(staging, target)
        except BaseException:
            if backup:
                os.rename(backup, target)
                backup = None
            raise
        if backup:
            shutil.rmtree(backup)
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging)
