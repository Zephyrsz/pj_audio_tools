from __future__ import annotations

from pathlib import Path
import re
import shutil
import tempfile

from .base import SequentialASR, logged_json, run, spans_from_json
from ..extract import AudioError
from ..models import TimedWord, Transcript, finite
from ..languages import language_name


class SoniqoBase:
    def __init__(self, config):
        self.config = config
        self.options = config.options
        if not isinstance(self.options.get("executable", "speech"), str):
            raise AudioError("executable 必须是字符串。")
        finite(self.options.get("timeout", 1800), "timeout", minimum=.1)

    def command(self, *args):
        return [self.options.get("executable", "speech"), *map(str, args)]

    def run(self, command):
        return run(command, timeout=self.options.get("timeout", 1800))

    def add_language(self, command, language):
        if language:
            command += ["--language", language_name(language)]
        return command


class SoniqoASR(SoniqoBase, SequentialASR):
    def transcribe(self, audio, language=None):
        return self.transcribe_many([audio], language)[0]

    def transcribe_many(self, audio, language=None):
        engine = self.options.get("engine", "qwen3")
        if engine not in {"qwen3", "parakeet"} or self.options.get("context"):
            results = []
            for path in audio:
                command = self.command("transcribe", path, "--engine", engine)
                if self.config.model:
                    command += ["--model", self.config.model]
                self.add_language(command, language)
                if self.options.get("context"):
                    command += ["--context", self.options["context"]]
                stdout = self.run(command)
                match = re.search(r"(?ms)^Result: (.*?)(?=\n  Time:|\Z)", stdout)
                if match is None:
                    raise AudioError("无法解析 Soniqo 转写；请检查 speech 版本。")
                results.append(Transcript(match.group(1).strip(), language))
            return results
        # Files written by --output-dir are more robust than JSONL mixed with logs.
        with tempfile.TemporaryDirectory(prefix="voice-tools-asr-") as directory:
            source, output = Path(directory) / "in", Path(directory) / "out"
            source.mkdir()
            for i, path in enumerate(audio):
                shutil.copyfile(path, source / f"{i:06d}.wav")
            command = self.command("transcribe-batch", source, "--output-dir", output,
                                   "--engine", engine, "--batch-size", "1")
            if self.config.model:
                command += ["--model", self.config.model]
            self.add_language(command, language)
            self.run(command)
            results = []
            for i in range(len(audio)):
                path = output / f"{i:06d}.txt"
                if not path.is_file():
                    raise AudioError(f"Soniqo 未返回第 {i + 1} 个工作块的转写。")
                results.append(Transcript(path.read_text(encoding="utf-8"), language))
            return results


class SoniqoAligner(SoniqoBase):
    def align(self, audio, text, language=None):
        if not language:
            raise AudioError("Soniqo 对齐需显式 language，避免日语默认按英语分词。")
        command = self.command("align", audio, "--text", text,
                               "--engine", self.options.get("engine", "mlx"))
        if self.config.model:
            command += ["--aligner-model", self.config.model]
        self.add_language(command, language)
        stdout = self.run(command)
        return [TimedWord(m[2], float(m[0]), float(m[1])) for m in re.findall(
            r"(?m)^\[(\d+(?:\.\d+)?)s\s*-\s*(\d+(?:\.\d+)?)s\] (.*)$", stdout)]


class SoniqoVAD(SoniqoBase):
    def detect(self, audio):
        engine = self.options.get("engine", "mlx")
        subcommand = "vad" if engine in {"pyannote", "firered"} else "vad-stream"
        command = self.command(subcommand, audio, "--engine", engine, "--json")
        if self.config.model:
            command += ["--model", self.config.model]
        for key in ("onset", "offset", "min_speech", "min_silence", "threshold", "smooth_window"):
            if key in self.options:
                command += ["--" + key.replace("_", "-"), str(self.options[key])]
        return spans_from_json(logged_json(self.run(command), list))


class SoniqoTTS(SoniqoBase):
    MODEL_FLAGS = {
        "qwen3": "--model", "cosyvoice": "--model-id",
        "voxcpm2": "--voxcpm2-variant", "indextts2": "--indextts2-model-id",
        "f5": "--f5-model-id", "higgs": "--higgs-model-id",
        "indic-mio": "--indic-mio-model-id", "magpie": "--magpie-variant",
        "magpie-coreml": "--magpie-variant",
    }

    def synthesize(self, text, output, language=None):
        engine = self.options.get("engine", "qwen3")
        if engine not in self.MODEL_FLAGS:
            raise AudioError(f"不支持的 Soniqo TTS engine：{engine}")
        command = self.command("speak", "--engine", engine, "-o", output)
        if self.config.model:
            command += [self.MODEL_FLAGS[engine], self.config.model]
        self.add_language(command, language)
        for key in ("speaker", "instruct", "voice_sample", "temperature", "max_tokens",
                    "cosy_reference_transcript", "f5_reference_text", "higgs_ref_text",
                    "magpie_speaker", "voxcpm2_prompt_text"):
            if key in self.options:
                command += ["--" + key.replace("_", "-"), str(self.options[key])]
        # Positional text after "--" cannot be interpreted as CLI options.
        command += ["--", text]
        self.run(command)
