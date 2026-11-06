"""新增工具与 pipeline 的命令行；不改变现有 extract/denoise/restore。"""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import sys

from .config import PipelineConfig
from .extract import AudioError
from .models import ToolConfig
from .pipeline import split_sentences
from .registry import default_registry
from .storage import dumps, read_json, write_json, write_text
from .tools import align_audio, complete_text, detect_speech, synthesize_text, transcribe_audio


def _text_arguments(parser):
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="输入文字")
    group.add_argument("--text-file", help="UTF-8 文字文件")


def _tool_arguments(parser, default="soniqo"):
    parser.set_defaults(default_provider=default)
    parser.add_argument("--config", help="单个工具的 JSON 配置文件")
    parser.add_argument("--provider", help="工具后端；查看 providers")
    parser.add_argument("--model", help="模型名称/ID，与后端独立选择")
    parser.add_argument("--engine", help="Soniqo 内部引擎")
    parser.add_argument("--executable", help="自定义 speech 可执行程序")
    parser.add_argument("--base-url", help="兼容 HTTP 服务地址，包含 /v1")
    parser.add_argument("--api-key-env", help="API 密钥的环境变量名称")
    parser.add_argument("--api-key-file", help="OpenRouter 密钥文件；默认 configs/or.cfg")
    parser.add_argument("--options", help="额外 options JSON 文件")
    parser.add_argument("--language", help="语言；Soniqo 用 Japanese，兼容 ASR 通常用 ja")
    parser.add_argument("-o", "--output", help="结果路径；ASR/VAD/align 默认 JSON 标准输出")
    parser.add_argument("--overwrite", action="store_true")


def add_commands(commands):
    commands.add_parser("providers", help="列出每项功能可选择的后端")
    from .batch_asr import add_arguments as add_batch_arguments
    add_batch_arguments(commands.add_parser("asr-batch", help="目录批量 ASR、时长过滤和数据集导出"))
    asr = commands.add_parser("asr", help="独立转写；不将 ASR 结果当词时间戳")
    asr.add_argument("input")
    _tool_arguments(asr)
    align = commands.add_parser("align", help="独立强制对齐并检查原文覆盖")
    align.add_argument("input")
    _text_arguments(align)
    _tool_arguments(align)
    vad = commands.add_parser("vad", help="独立检测活动区间")
    vad.add_argument("input")
    _tool_arguments(vad)
    llm = commands.add_parser("llm", help="独立调用文本模型")
    _text_arguments(llm)
    llm.add_argument("--system", help="系统提示")
    _tool_arguments(llm, "openai-compatible")
    tts = commands.add_parser("tts", help="独立生成 WAV；可选不同工具/模型")
    _text_arguments(tts)
    _tool_arguments(tts)
    pipeline = commands.add_parser("sentences", aliases=["pipeline"], help="长音频切句与字幕 pipeline")
    pipeline.add_argument("input")
    pipeline.add_argument("--config", help="完整 pipeline JSON 配置")
    pipeline.add_argument("-o", "--output", help="独立任务目录；导出在其 dataset 子目录")
    pipeline.add_argument("--language")
    pipeline.add_argument("--start", default="0", help="原录音上的起点")
    pipeline.add_argument("--end", help="原录音上的终点；省略处理至尾部")
    pipeline.add_argument("--resume", action="store_true", help="恢复同一输入/范围的已有任务")
    pipeline.add_argument("--window-seconds", type=float)
    pipeline.add_argument("--context-seconds", type=float)
    pipeline.add_argument("--channel", choices=["mix", "0", "1"])
    pipeline.add_argument("--keep-silence", action="store_true", help="不依据 VAD 跳过窗口")
    pipeline.add_argument("--no-llm", action="store_true")
    pipeline.add_argument("--no-tts", action="store_true")
    for kind in ("asr", "aligner", "vad", "llm", "tts"):
        pipeline.add_argument(f"--{kind}-provider")
        pipeline.add_argument(f"--{kind}-model")
        if kind != "llm":
            pipeline.add_argument(f"--{kind}-engine")


def _text(args):
    if args.text is not None:
        return args.text
    try:
        return Path(args.text_file).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AudioError(f"无法读取文字文件：{exc}") from exc


def _tool_config(args):
    spec = ToolConfig.from_dict(read_json(Path(args.config))) if args.config else ToolConfig(args.default_provider)
    options = dict(spec.options)
    if args.options:
        extra = read_json(Path(args.options))
        if not isinstance(extra, dict):
            raise AudioError("options 文件必须为 JSON 对象。")
        options.update(extra)
    for name in ("engine", "executable", "base_url", "api_key_env", "api_key_file"):
        value = getattr(args, name, None)
        if value is not None:
            options[name] = value
    return ToolConfig(args.provider or spec.provider,
                      args.model if args.model is not None else spec.model, options)


def _pipeline_config(args):
    config = PipelineConfig.load(args.config) if args.config else PipelineConfig()
    values = {}
    for key in ("language", "window_seconds", "context_seconds", "channel"):
        value = getattr(args, key)
        if value is not None:
            values[key] = int(value) if key == "channel" and value != "mix" else value
    if args.keep_silence:
        values["skip_silence"] = False
    for kind in ("asr", "aligner", "vad", "llm", "tts"):
        provider = getattr(args, kind + "_provider")
        model = getattr(args, kind + "_model")
        engine = getattr(args, kind + "_engine", None)
        if provider is not None or model is not None or engine is not None:
            spec = getattr(config, kind) or ToolConfig("openai-compatible" if kind == "llm" else "soniqo")
            options = dict(spec.options)
            if engine:
                options["engine"] = engine
            values[kind] = ToolConfig(provider or spec.provider, model if model is not None else spec.model, options)
    if args.no_llm:
        values["llm"] = None
    if args.no_tts:
        values["tts"] = None
    return replace(config, **values)


def handle(args):
    if args.command == "asr-batch":
        from .batch_asr import handle as handle_batch
        return handle_batch(args)
    if args.command == "providers":
        print(dumps(default_registry().available()))
        return
    if args.command in {"sentences", "pipeline"}:
        result = split_sentences(
            args.input, args.output, config=_pipeline_config(args), start=args.start,
            end=args.end, resume=args.resume,
            progress=lambda status: print(f"正在处理：{status}", file=sys.stderr, flush=True))
        print(f"已保存数据集：{result}")
        return
    config = _tool_config(args)
    if args.output and hasattr(args, "input"):
        source, output = Path(args.input).expanduser().resolve(), Path(args.output).expanduser()
        if (source == output.resolve() or
                (source.exists() and output.exists() and source.samefile(output))):
            raise AudioError("输出不能覆盖原录音。")
    if args.command == "asr":
        result = asdict(transcribe_audio(args.input, config=config, language=args.language))
    elif args.command == "vad":
        result = {"spans": [asdict(s) for s in detect_speech(args.input, config=config)]}
    elif args.command == "align":
        result = {"units": [asdict(u) for u in align_audio(
            args.input, _text(args), config=config, language=args.language)]}
    elif args.command == "llm":
        result = complete_text(_text(args), config=config, system=args.system)
        if args.output:
            write_text(Path(args.output), result + "\n", overwrite=args.overwrite)
        else:
            print(result)
        return
    elif args.command == "tts":
        if not args.output:
            raise AudioError("TTS 需要通过 -o 指定输出 WAV。")
        result = synthesize_text(_text(args), args.output, config=config,
                                 language=args.language, overwrite=args.overwrite)
        print(f"已保存：{result}")
        return
    else:
        raise AudioError(f"未知命令：{args.command}")
    if args.output:
        write_json(Path(args.output), result, overwrite=args.overwrite)
        print(f"已保存：{Path(args.output).absolute()}")
    else:
        print(dumps(result))
