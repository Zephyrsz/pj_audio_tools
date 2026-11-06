# 语音增强样例验收

日期：2026-11-04（Asia/Shanghai）。

环境：Apple Silicon macOS，Python 3.14.7，Homebrew speech 0.0.23。程序要求 Python 3.10+，但本次只在上述 Python 版本执行。

## 输入

`outputs/20261028井上和_0-100.wav`

- 100.000000 秒、44,100 Hz、双声道、PCM 16-bit。
- SHA-256：`fc4df36ca86f6c59f6145c9ed63746e034ebe55b70a35c695d5d5eddd38926c8`。
- 两次模型调用前后均比较散列，原录音未变。

## 实际运行

在项目根目录执行：

```bash
python3 -m voice_tools denoise 'outputs/20261028井上和_0-100.wav' --overwrite
python3 -m voice_tools restore 'outputs/20261028井上和_0-100.wav'
```

第一条使用 `--overwrite` 是因为前期调研已经生成了降噪结果。两条命令均直接读取原音频，退出码为 0。

| 项目 | DeepFilterNet3 | Sidon fp16 |
|---|---:|---:|
| 输出后缀 | `_denoised.wav` | `_restored.wav` |
| 时长 | 100.000000 秒 | 99.794583 秒 |
| 采样率 | 48,000 Hz | 48,000 Hz |
| 声道 | 1 | 1 |
| 编码 | pcm_s16le | pcm_s16le |
| 命令总耗时 | 17.099 秒 | 144.796 秒 |
| 完整解码 | 通过 | 通过 |
| 源文件散列 | 未变 | 未变 |

耗时为本次命令的总墙钟时间。DeepFilterNet3 使用已缓存模型；Sidon 包含首次下载和加载，不能据此比较两个模型的纯推理速度。

Sidon 输出比原录音短约 0.205417 秒，这是本次 Soniqo 输出的实际样本数差异；本工具保留原样，没有填零、变速或修改语音内容来凑时长。不要直接把该文件当作需要采样精确对齐的原始轨道替身。

## 自动化验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q voice_tools tests
ffprobe -v error -show_entries format=duration:stream=codec_name,sample_rate,channels -of json 'outputs/20261028井上和_0-100_denoised.wav'
ffprobe -v error -show_entries format=duration:stream=codec_name,sample_rate,channels -of json 'outputs/20261028井上和_0-100_restored.wav'
ffmpeg -v error -nostdin -i 'outputs/20261028井上和_0-100_denoised.wav' -f null -
ffmpeg -v error -nostdin -i 'outputs/20261028井上和_0-100_restored.wav' -f null -
```

30 项测试通过：新增增强模块 18 项，原有截取模块 12 项。测试覆盖真实的 fake-speech 子进程调用、参数传递、中文/空格路径、默认输出、显式覆盖、输入别名保护、竞争写入保护、失败/中断清理、清理失败时保留原始错误、空或损坏 WAV 拒绝发布，以及现有截取功能回归。

## 听感评价边界

以上验收只证明功能调用、文件保护、输出结构和可解码性。未给出盲听评分，也没有干净的人声参考轨，不能据此声称纸张声已彻底消除或哪一个版本音色一定更好。

试听请对比讲话与书写重叠处、翻页瞬间，以及齿音、轻辅音和呼吸；同时注意 Sidon 的重建感和分块处连续性。优先保留语音自然清楚、残留噪声可接受的版本。
