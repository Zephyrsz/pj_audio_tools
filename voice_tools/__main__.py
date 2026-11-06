"""命令行入口：python3 -m voice_tools。"""

import argparse
import sys

from . import AudioError, denoise_audio, extract_clip, restore_audio


def main() -> int:
    parser = argparse.ArgumentParser(description="语音处理工具集")
    commands = parser.add_subparsers(dest="command", required=True)
    extract = commands.add_parser("extract", help="按开始和结束时间截取音频")
    extract.add_argument("input", help="原音频文件路径")
    extract.add_argument("--start", required=True, help="开始时间：秒数、MM:SS 或 HH:MM:SS")
    extract.add_argument("--end", required=True, help="结束时间（非片段长度），支持小数秒")
    extract.add_argument("-o", "--output", help="输出路径；默认保存到 outputs 目录，格式为 WAV")
    extract.add_argument("--overwrite", action="store_true", help="允许覆盖已有输出文件")
    denoise = commands.add_parser("denoise", help="用 DeepFilterNet3 去除语音背景噪声")
    restore = commands.add_parser("restore", help="用 Sidon 降噪、去混响并重建语音")
    for command in (denoise, restore):
        command.add_argument("input", help="原音频路径；建议使用 WAV")
        command.add_argument("-o", "--output", help="输出 WAV 路径；默认保存到 outputs 目录")
        command.add_argument("--overwrite", action="store_true", help="允许覆盖已有输出文件")
        command.add_argument("--model", help="Hugging Face 模型 ID；默认由 speech 选择")
    denoise.add_argument("--chunk-seconds", type=float, help="分块秒数；默认由 speech 选择")
    denoise.add_argument("--overlap-ms", type=int, help="分块交叉淡化毫秒数")
    denoise.add_argument("--no-chunk", action="store_true", help="禁用自动分块，长音频可能报错")
    restore.add_argument("--variant", choices=("fp16", "int8"), help="Sidon 模型精度")
    from .cli import add_commands, handle
    add_commands(commands)
    args = parser.parse_args()
    try:
        if args.command == "extract":
            output = extract_clip(args.input, args.start, args.end, args.output,
                                  overwrite=args.overwrite)
        elif args.command == "denoise":
            print("正在降噪；首次运行可能需要下载模型，请稍候……", flush=True)
            output = denoise_audio(args.input, args.output, overwrite=args.overwrite,
                                   model=args.model, chunk_seconds=args.chunk_seconds,
                                   overlap_ms=args.overlap_ms, no_chunk=args.no_chunk)
        elif args.command == "restore":
            print("正在修复；首次运行可能需要下载模型，请稍候……", flush=True)
            output = restore_audio(args.input, args.output, overwrite=args.overwrite,
                                   model=args.model, variant=args.variant)
        else:
            handle(args)
            return 0
    except AudioError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消处理。", file=sys.stderr)
        return 130
    print(f"已保存：{output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
