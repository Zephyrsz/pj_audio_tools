# 语音增强与修复设计

在现有 FFmpeg 音频截取工具之外增加语音增强模块，调用本机 Soniqo `speech` CLI 生成以保留人声、抑制背景噪声为目标的降噪或修复版本。不能保证完全移除与语音重叠的噪声。第一版不把音乐源分离模型用于纸张、翻书和书写声，也不默认串联两个模型。

## 接口

- `denoise_audio(input_path, output_path=None, *, overwrite=False, executable="speech", model=None, chunk_seconds=None, overlap_ms=None, no_chunk=False) -> Path`
- `restore_audio(input_path, output_path=None, *, overwrite=False, executable="speech", model=None, variant=None) -> Path`
- 默认输出分别为 `outputs/<stem>_denoised.wav` 和 `outputs/<stem>_restored.wav`。
- 输入文件必须存在；输出必须为 PCM WAV；默认不覆盖已有文件；输出不能与输入文件相同，包括指向输入的软链接或硬链接。
- 处理先写到输出目录的临时 WAV，Soniqo 成功后流式检查 WAV 完整性并原子发布；失败时保留已有输出并清理临时文件。

## 处理策略

- `denoise_audio` 调用 `speech denoise`，适合作为纸张摩擦和连续环境噪声的第一选择。默认不传可选参数，使用 Soniqo 自身的 45 秒分块和 500 ms 交叉淡化。
- `restore_audio` 调用 `speech restore`，适合作为独立的去混响/重建备选版本；不接受前一个输出作为隐式输入。
- Soniqo 产生的采样率、声道数、时长和重建特性由工具决定；模块不重新采样、混音或填充/拉伸时长。用户样例在 speech 0.0.23 上的 Sidon 输出比输入短约 0.205 秒，不能用于采样精确对齐的替换。

## 命令行

`python3 -m voice_tools denoise INPUT [-o OUTPUT] [--overwrite]` 和 `restore` 对应 Python 接口。可选模型、分块及 Sidon variant 参数只在对应子命令上出现。

## 错误与验证

缺少 `speech`、输入不可读、非法数值/模型参数、输出格式错误、输出冲突、子进程失败或未生成有效输出时抛出 `AudioError`；CLI 打印中文错误并返回非零状态。单元测试使用假的 speech 可执行文件/子进程验证边界；真实样例分别运行 denoise 与 restore，用 ffprobe 检查时长/格式并完整解码，比较处理前后源文件散列。技术验收不代替听感评价。
