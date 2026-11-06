"""工作窗口与句子切点的时间规则；纯函数，不读写文件、不调用模型。"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import replace

from .audio import silence_midpoints
from .config import PipelineConfig
from .extract import AudioError
from .models import Sentence, Span, Unit
from .segmentation import normalized


def plan_windows(total: float, spans: list[Span], config: PipelineConfig) -> list[dict]:
    points = silence_midpoints(spans, total)
    windows, begin = [], 0.0
    while begin < total - .00001:
        ideal = min(total, begin + config.window_seconds)
        end = ideal
        if ideal < total:
            choices = [p for p in points
                       if max(begin + .1, ideal - config.boundary_search_seconds) <= p
                       <= min(total, ideal + config.boundary_search_seconds)]
            if choices:
                end = min(choices, key=lambda p: abs(p - ideal))
        # A tiny final remainder has little linguistic context and is prone to
        # hallucinated trailing words. Keep it with the preceding work window.
        if 0 < total - end < min(5, config.window_seconds / 3):
            end = total
        left, right = max(0, begin - config.context_seconds), min(total, end + config.context_seconds)
        active = any(s.end > begin and s.start < end for s in spans)
        windows.append({"id": len(windows), "core_start": begin, "core_end": end,
                        "start": left, "end": right,
                        "skip": config.skip_silence and not active})
        begin = end
    return windows


def merge_units(chunks: list[tuple[dict, list[Unit]]]) -> list[Unit]:
    result = []
    for window, units in chunks:
        owned = [u for u in units if
                 window["core_start"] <= (u.start + u.end) / 2 < window["core_end"]]
        if result and owned:
            # A word crossing the core boundary can be absent from the previous
            # transcript. Keep it when its audio fits the uncovered gap.
            rescued = [replace(u, flags=(*u.flags, "window_boundary_rescued"))
                       for u in units if u not in owned
                       and u.start < window["core_start"] < u.end
                       and u.start >= result[-1].end - .081
                       and u.end > result[-1].end + .011
                       and u.end <= owned[0].start + .011]
            owned = rescued + owned
        if units and not owned and window["core_end"] - window["core_start"] > .5:
            raise AudioError("工作窗口正文没有对齐词，拒绝仅凭上下文转写生成空结果。")
        appended = False
        for owned_index, unit in enumerate(owned):
            if result and unit.start < result[-1].end - .011:
                previous = result[-1]
                overlap = min(previous.end, unit.end) - max(previous.start, unit.start)
                if (normalized(previous.text) == normalized(unit.text) and overlap > 0 and
                        abs(previous.start - unit.start) < .25):
                    continue
                if previous.end - unit.start <= .081:
                    join = max(previous.start, (previous.end + unit.start) / 2)
                    if join <= unit.end:
                        result[-1] = replace(previous, end=join,
                                             flags=(*previous.flags, "alignment_overlap_adjusted"))
                        unit = replace(unit, start=join,
                                       flags=(*unit.flags, "alignment_overlap_adjusted"))
                    else:
                        raise AudioError("窗口边界的时间冲突无法安全修正。")
                else:
                    raise AudioError("工作窗口交界的文字/时间冲突；请调整窗口或 context_seconds 后重试。")
            flags = list(unit.flags)
            if (any(abs((unit.start + unit.end) / 2 - point) < .12
                    for point in (window["core_start"], window["core_end"]))
                    or (not appended and window["core_start"] > 0)
                    or (owned_index == len(owned) - 1 and window["core_end"] < chunks[-1][0]["core_end"])):
                flags.append("window_boundary")
            text = unit.text
            if (not appended and result and result[-1].text and text and
                    result[-1].text[-1].isascii() and result[-1].text[-1].isalnum() and
                    text[0].isascii() and text[0].isalnum()):
                text = " " + text
            result.append(replace(unit, id=len(result) + 1, text=text, flags=tuple(flags)))
            appended = True
    return result


def refine_edges(sentences: list[Sentence], frames) -> list[Sentence]:
    """只在已对齐词之外的低能量区寻找邻近切点，不穿过讲话。

    frames 为 (start, end, db) 序列，通常来自 audio.energy_frames。
    """
    quiet = [(a + b) / 2 for a, b, db in frames if db < -45]
    result = []
    for i, sentence in enumerate(sentences):
        previous_end = sentences[i - 1].speech_end if i else 0
        next_start = sentences[i + 1].speech_start if i + 1 < len(sentences) else float("inf")
        cuts = []
        for nominal, lower, upper in [
            (sentence.cut_start, max(previous_end, sentence.cut_start - .08), sentence.speech_start),
            (sentence.cut_end, sentence.speech_end, min(next_start, sentence.cut_end + .08)),
        ]:
            ix = bisect_left(quiet, nominal)
            choices = [v for v in quiet[max(0, ix - 5):ix + 5]
                       if lower <= v <= upper and abs(v - nominal) <= .08]
            cuts.append(min(choices, key=lambda v: abs(v - nominal)) if choices else nominal)
        result.append(replace(sentence, cut_start=cuts[0], cut_end=cuts[1]))
    for i in range(len(result) - 1):
        a, b = result[i:i + 2]
        if a.cut_end > b.cut_start:
            midpoint = (a.speech_end + b.speech_start) / 2
            result[i] = replace(a, cut_end=min(a.cut_end, midpoint))
            result[i + 1] = replace(b, cut_start=max(b.cut_start, midpoint))
    return result
