# Audio Extraction Implementation Plan

**Goal:** 提供按起止时间截取音频的命令行与 Python 接口，并输出指定原音频前 100 秒。

**Architecture:** Python 标准库负责时间、路径校验和命令行，FFmpeg 负责精确定位并编码，ffprobe 提供源音频信息。独立 `voice_tools/extract.py` 可被后续模块复用。

**Tech Stack:** Python 3.10+、FFmpeg、ffprobe、unittest。

**Spec:** `docs/superpowers/specs/2026-11-04-audio-extraction-design.md`

## 执行步骤

- [x] 创建 `tests/test_extract.py`：时间解析、PCM 样本截取对比、AAC 输入、四种输出格式、非法区间、原文件与已有输出保护、命令行调用。执行 `python3 -m unittest discover -s tests -v`，确认尚未实现时失败。
- [x] 创建 `voice_tools/extract.py`，实现 `parse_time(value) -> float` 和 `extract_clip(input_path, start, end, output_path=None, *, overwrite=False) -> Path`；创建 `__init__.py` 导出接口；创建 `__main__.py` 实现 `python3 -m voice_tools extract INPUT --start START --end END [-o OUTPUT] [--overwrite]`。
- [x] 执行全套测试并修正失败，确保测试对比真实输出内容而非命令拼接。
- [x] 添加 `pyproject.toml`、`.gitignore` 和中文 `README.md`，包括安装依赖、命令行、Python 调用、格式与误差说明。
- [x] 用真实文件运行 `python3 -m voice_tools extract 'raw/20261028井上和.m4a' --start 0 --end 100`，使用 ffprobe 验证 100 秒输出，并用 FFmpeg 完整解码检查。

当前目录不是 Git 仓库，直接在用户指定目录完成，不创建分支或提交。
