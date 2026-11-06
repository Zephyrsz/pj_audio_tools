"""切句任务流：prepare → vad → asr → align → segment → export 的阶段编排。

各阶段通过参数和返回值传递数据；业务规则在 timeline / segmentation，
音频与文件操作在 audio / storage，缓存键在 cache_keys。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import os
from pathlib import Path
import shutil
import uuid

from .audio import convert, energy_frames, slice_pcm, validate_wav
from .cache import StageCache
from .cache_keys import PIPELINE_VERSION, provider_key
from .config import PipelineConfig
from .export import export_dataset
from .extract import AudioError
from .models import Span
from .providers.base import spans_from_json, transcript_from_json, words_from_json
from .segmentation import align_units, llm_boundaries, normalized, plan_sentences, validate_boundaries
from .storage import digest, file_hash, write_json
from .timeline import merge_units, plan_windows, refine_edges
from .tools import synthesize_text
from .workspace import TaskWorkspace


@dataclass(frozen=True)
class FlowContext:
    """一次任务的只读依赖；providers 按功能名索引。"""
    config: PipelineConfig
    registry: object
    providers: dict
    workspace: TaskWorkspace
    source: Path
    identity: dict
    cache: StageCache

    @property
    def task(self) -> Path:
        return self.workspace.root

    @property
    def base(self) -> dict:
        return {"source": self.identity, "pipeline_version": PIPELINE_VERSION}

    def key(self, kind: str) -> dict:
        return provider_key(kind, getattr(self.config, kind), self.providers[kind])


def _window_path(ctx: FlowContext, window: dict) -> Path:
    path = ctx.task / "work" / f"window-{window['id']:06d}.wav"
    if path.is_symlink():
        raise AudioError("工作窗口不能是软链接。")
    return path


def prepare_analysis(ctx: FlowContext) -> tuple[Path, float]:
    """生成 16 kHz 单声道分析音轨，返回路径和处理范围内时长。"""
    identity, cache = ctx.identity, ctx.cache
    analysis = ctx.task / "work" / "analysis.wav"
    cached = cache.get("prepare", ctx.base)
    if analysis.is_symlink():
        raise AudioError("分析音频不能是软链接。")
    if cached and analysis.is_file():
        if file_hash(analysis) != cached["sha256"]:
            raise AudioError("分析音轨已改变，请移走 work/analysis.wav 后恢复。")
        analysis_duration = validate_wav(analysis)
    else:
        temporary = ctx.task / "work" / ("analysis-" + uuid.uuid4().hex + ".wav")
        try:
            convert(ctx.source, temporary, start=identity["start"], end=identity["end"],
                    mono=True, sample_rate=16000, channel=ctx.config.channel)
            analysis_duration = validate_wav(temporary)
            if abs(analysis_duration - (identity["end"] - identity["start"])) > .03:
                raise AudioError("分析音轨与原时间轴时长不一致。")
            os.replace(temporary, analysis)
        finally:
            temporary.unlink(missing_ok=True)
        cache.put("prepare", ctx.base, {"sha256": file_hash(analysis)})
    return analysis, min(analysis_duration, identity["end"] - identity["start"])


def detect_spans(ctx: FlowContext, analysis: Path, analysis_duration: float) -> list[Span]:
    key = {**ctx.base, "provider": ctx.key("vad")}
    data = ctx.cache.get("vad", key)
    if data is None:
        spans = ctx.providers["vad"].detect(analysis)
        data = [asdict(s) for s in spans]
        spans_from_json(data)
        ctx.cache.put("vad", key, data)
    spans = spans_from_json(data)
    if any(s.end > analysis_duration + .032 for s in spans):
        raise AudioError("VAD 时间超出分析音轨。")
    return [Span(s.start, min(s.end, analysis_duration)) for s in spans if s.start < analysis_duration]


def transcribe_windows(ctx: FlowContext, analysis: Path, active: list[dict]) -> tuple[dict, dict]:
    """返回 (窗口 id → Transcript, 本次切出的窗口 WAV 路径)。"""
    config, cache = ctx.config, ctx.cache
    transcripts, paths, missing = {}, {}, []
    for window in active:
        key = {**ctx.base, "window": window, "language": config.language,
               "provider": ctx.key("asr")}
        data = cache.get("asr", key)
        if data is None:
            missing.append((window, key))
        else:
            transcripts[window["id"]] = transcript_from_json(data)
    for offset in range(0, len(missing), config.asr_group_size):
        group = missing[offset:offset + config.asr_group_size]
        inputs = []
        for window, _ in group:
            path = _window_path(ctx, window)
            slice_pcm(analysis, path, window["start"], window["end"])
            inputs.append(path)
            paths[window["id"]] = path
        asr = ctx.providers["asr"]
        if hasattr(asr, "transcribe_many"):
            results = asr.transcribe_many(inputs, config.language)
        else:
            results = [asr.transcribe(p, config.language) for p in inputs]
        if len(results) != len(group):
            raise AudioError("ASR 返回数量与请求窗口数量不一致。")
        for (window, key), result in zip(group, results):
            validated = transcript_from_json(asdict(result))
            transcripts[window["id"]] = validated
            cache.put("asr", key, asdict(validated))
        ctx.workspace.progress(f"asr {min(offset + len(group), len(missing))}/{len(missing)}")
    return transcripts, paths


def align_windows(ctx: FlowContext, analysis: Path, active: list[dict], spans: list[Span],
                  transcripts: dict, paths: dict) -> tuple[list, list[dict]]:
    """返回 (按窗口的全局时间轴单元, 对齐到窗口音频之外的文字)。"""
    config, cache, identity = ctx.config, ctx.cache, ctx.identity
    chunks, alignment_issues = [], []
    for window in active:
        text = transcripts[window["id"]].text
        if not normalized(text):
            if any(s.end > window["core_start"] and s.start < window["core_end"] for s in spans):
                raise AudioError(f"窗口 {window['id']} 检测到活动但没有有效转写，请检查 ASR。")
            chunks.append((window, []))
            continue
        key = {**ctx.base, "window": window, "text": text, "language": config.language,
               "provider": ctx.key("aligner")}
        data = cache.get("align", key)
        uncached = data is None
        if data is None:
            path = paths.get(window["id"], ctx.task / "work" / f"window-{window['id']:06d}.wav")
            if path.is_symlink():
                raise AudioError("工作窗口不能是软链接。")
            slice_pcm(analysis, path, window["start"], window["end"])
            words = ctx.providers["aligner"].align(path, text, config.language)
            data = [asdict(w) for w in words]
        diagnostic = ctx.task / "logs" / f"align-window-{window['id']:06d}.json"
        write_json(diagnostic, {"window": window, "text": text, "words": data}, overwrite=True)
        try:
            units = align_units(text, words_from_json(data), window["end"] - window["start"],
                                allow_outside_tail=True,
                                core_end=window["core_end"] - window["start"])
        except AudioError as exc:
            raise AudioError(f"窗口 {window['id']} 对齐失败：{exc} "
                             f"原始结果：{diagnostic}") from exc
        outside = [u for u in units if "outside_audio" in u.flags
                   or "context_tail_overrun" in u.flags]
        if len(outside) > 3 or sum(len(normalized(u.text)) for u in outside) > 12:
            raise AudioError("过多转写文字被对齐到音频之外，拒绝丢弃；请检查 ASR/对齐模型。")
        if uncached:
            cache.put("align", key, data)
        for unit in outside:
            alignment_issues.append({
                "window_id": window["id"], "text": unit.text,
                "start": unit.start + window["start"] + identity["start"],
                "end": unit.end + window["start"] + identity["start"],
                "reason": ("context_tail_exceeds_window_audio" if "context_tail_overrun" in unit.flags
                           else "aligned_entirely_after_window_audio"),
            })
        global_units = [replace(u, start=u.start + window["start"],
                                end=u.end + window["start"]) for u in units]
        chunks.append((window, global_units))
        ctx.workspace.progress(f"align {len(chunks)}/{len(active)}")
    return chunks, alignment_issues


def segment(ctx: FlowContext, units: list, analysis: Path, analysis_duration: float,
            active: list[dict], alignment_issues: list[dict]) -> list:
    """分句并换算到原录音时间轴，附加范围与未指派尾文标记。"""
    config, identity = ctx.config, ctx.identity
    boundaries = None
    if config.llm and units:
        key = {"units": [asdict(u) for u in units], "provider": ctx.key("llm"),
               "prompt_version": 1}
        boundaries = ctx.cache.get("segment", key)
        if boundaries is None:
            boundaries = llm_boundaries(units, ctx.providers["llm"])
            ctx.cache.put("segment", key, boundaries)
        validate_boundaries(boundaries, len(units))
    sentences = plan_sentences(units, duration=analysis_duration, boundaries=boundaries,
                               pad_before=config.pad_before, pad_after=config.pad_after,
                               max_seconds=config.max_sentence_seconds)
    sentences = refine_edges(sentences, energy_frames(analysis))
    shift = identity["start"]
    sentences = [replace(s, speech_start=s.speech_start + shift, speech_end=s.speech_end + shift,
                         cut_start=s.cut_start + shift,
                         cut_end=min(identity["end"], s.cut_end + shift)) for s in sentences]
    if sentences and identity["start"] > 0:
        sentences[0] = replace(sentences[0], flags=(*sentences[0].flags, "range_start"))
    if sentences and identity["end"] < identity["source_duration"]:
        sentences[-1] = replace(sentences[-1], flags=(*sentences[-1].flags, "range_end"))
    if sentences and alignment_issues and any(
            issue["window_id"] == active[-1]["id"] for issue in alignment_issues):
        sentences[-1] = replace(sentences[-1], flags=(*sentences[-1].flags, "unassigned_tail_text"))
    return sentences


def export(ctx: FlowContext, sentences: list, alignment_issues: list[dict]) -> Path:
    config, cache = ctx.config, ctx.cache
    tts_identity = ctx.key("tts") if config.tts else None

    def synthesize(text, output):
        key = {"text": text, "language": config.language, "provider": tts_identity}
        cached_audio = ctx.task / "cache" / ("tts-" + digest(key) + ".wav")
        data = cache.get("tts", key)
        if cached_audio.is_symlink():
            raise AudioError("TTS 缓存不能是软链接。")
        if data and cached_audio.is_file():
            if file_hash(cached_audio) != data["sha256"]:
                raise AudioError("TTS 音频缓存损坏。")
            validate_wav(cached_audio)
        else:
            synthesize_text(text, cached_audio, config=config.tts, language=config.language,
                            overwrite=True, registry=ctx.registry)
            cache.put("tts", key, {"sha256": file_hash(cached_audio)})
        shutil.copyfile(cached_audio, output)

    return export_dataset(ctx.source, ctx.task, sentences, ctx.identity, config, ctx.registry,
                          synthesize=synthesize if config.tts else None, issues=alignment_issues,
                          tts_identity=tts_identity)


def run_sentence_flow(ctx: FlowContext) -> Path:
    """按固定顺序执行全部阶段，写出中间结果，返回 dataset 目录。"""
    config, task, stage = ctx.config, ctx.task, ctx.workspace.stage
    with stage("prepare"):
        analysis, analysis_duration = prepare_analysis(ctx)
    with stage("vad", tool=config.vad.to_dict()):
        spans = detect_spans(ctx, analysis, analysis_duration)
    windows = plan_windows(analysis_duration, spans, config)
    write_json(task / "windows.json", windows, overwrite=True)
    active = [w for w in windows if not w["skip"]]
    with stage("asr", tool=config.asr.to_dict(), windows=len(active)):
        transcripts, paths = transcribe_windows(ctx, analysis, active)
    raw = [{"window": w, **asdict(transcripts[w["id"]])} for w in active]
    write_json(task / "transcript.raw.json", raw, overwrite=True)
    with stage("align", tool=config.aligner.to_dict()):
        chunks, alignment_issues = align_windows(ctx, analysis, active, spans, transcripts, paths)
    units = merge_units(chunks)
    shift = ctx.identity["start"]
    write_json(task / "alignment.json", {"text": "".join(u.text for u in units),
               "units": [{**asdict(u), "start": u.start + shift, "end": u.end + shift} for u in units],
               "time_origin": "source", "unassigned_tail": alignment_issues}, overwrite=True)
    with stage("segment", tool=config.llm.to_dict() if config.llm else {"provider": "rules"}):
        sentences = segment(ctx, units, analysis, analysis_duration, active, alignment_issues)
        write_json(task / "sentences.json", [asdict(s) for s in sentences], overwrite=True)
    with stage("export", count=len(sentences), tts=config.tts.to_dict() if config.tts else None):
        return export(ctx, sentences, alignment_issues)
