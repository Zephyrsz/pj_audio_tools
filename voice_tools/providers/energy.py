from __future__ import annotations

from ..audio import energy_frames
from ..models import Span, finite


class EnergyVAD:
    """无需模型的能量门限基线；非人声分类器，噪声也可能被判为有声。"""
    def __init__(self, config):
        options = config.options
        self.db = options.get("threshold_db", -42)
        if isinstance(self.db, bool) or not isinstance(self.db, (int, float)) or not -100 <= self.db <= 0:
            from ..extract import AudioError
            raise AudioError("threshold_db 需在 -100 到 0 之间。")
        self.silence = finite(options.get("min_silence", .3), "min_silence")
        self.minimum = finite(options.get("min_speech", .08), "min_speech")

    def detect(self, audio):
        spans, start, last = [], None, 0.0
        for begin, end, db in energy_frames(audio):
            if db >= self.db:
                if start is None:
                    start = begin
                last = end
            elif start is not None and end - last >= self.silence:
                if last - start >= self.minimum:
                    spans.append(Span(start, last))
                start = None
        if start is not None and last - start >= self.minimum:
            spans.append(Span(start, last))
        return spans
