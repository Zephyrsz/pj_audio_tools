# 长音频切句 pipeline 验收记录

日期：2026-11-05。项目：voice-tools 0.3.0。验证对象为本地模块化工具及切句链路，不代表整片转写准确率或训练数据质量验收。

## 环境与输入

- 本机：Apple M5 Max，36 GB内存；Python 3.14.7，speech 0.0.23，FFmpeg/ffprobe。
- 原文件：`raw/20261028井上和.m4a`，6126.724354秒，44,100 Hz双声道AAC。
- 原文件SHA-256：`79c5d76f2584f4879866f4777e5d0f403654cac457bc29f2603c3013a2e7db1c`。
- 验收时配置：`configs/sentences.soniqo.json` 当时使用 Soniqo Qwen3-ASR-0.6B；当前配置已切换到 Qwen3-ASR-1.7B。对齐仍为 MLX Qwen3-ForcedAligner-0.6B-4bit，VAD 为 Silero；规则分句，LLM/TTS关闭。

## 真实模型运行

```bash
python3 -m voice_tools sentences 'raw/20261028井上和.m4a' \
  --config configs/sentences.soniqo.json --end 100 \
  -o outputs/sentences_100s_validation_20261105
```

首次运行已经完成。重新检查时使用相同命令并加`--resume`，不可直接写入已有非空任务目录。另有20–40秒短样本位于`outputs/sentences_validation_20261105/dataset/`，共6段，其中3段带复核标记；截取范围的首末句可能不完整。

100秒结果：

| 项目 | 结果 |
|---|---|
| 正文工作窗口 | 4个，0–24、24–47.376、47.376–71.184、71.184–100秒 |
| 音文配对 | 25个WAV、25个TXT、25个单句SRT、25行UTF-8清单 |
| 全局字幕 | 25条，使用原文件时间轴 |
| 复核清单 | 10段；不代表其余15段已验证正确 |
| 无法分配音频的尾文 | 1处，详见alignment_issues.json |
| WAV格式 | 44,100 Hz、双声道、16位PCM，全部完整解码通过 |
| 切片时长合计 | 98.543991秒，包含padding；不应解读为语音覆盖率 |
| 字幕与切片范围 | 全部合法，单句字幕不超出对应WAV |
| manifest与清单 | 音文一一对应，所有产物散列匹配 |
| 原文件 | 发布后及再次恢复后SHA-256均未变 |
| 缓存恢复 | ASR、对齐缓存及dataset/manifest.json的修改时间保持不变 |

机读验证结果：`outputs/sentences_100s_validation_20261105/validation.json`。处理日志：任务目录的`logs/process.jsonl`。中间结果：`transcript.raw.json`、`alignment.json`、`sentences.json`和`windows.json`。

## 本样本暴露的边界情况

- 对齐存在零时长词，涉及7段。完整零时长的感叹句`あら。`并入后句，文字和原始词级时间保留，增加复核标记。
- 1段存在跨窗口40ms时间冲突，按中点接合；1段救回跨正文边界但前窗未覆盖的上下文词。这两种处理均有专门标记。
- 最后一个窗口的转写`あろ。`被模型完全对齐到窗口音频之外，源时间约100.024–100.264秒。没有为它生成伪造片段；原文字和时间保存在审计文件，末句标记`unassigned_tail_text`。应结合原录音判断是错字、幻觉还是边界截断，不能直接把自动输出当作全转写真值。
- 源范围100秒处仍可能在一句话中间，最后片段标记`range_end`。规则分句只根据模型标点；若需更好的口语语义边界，可在配置中启用实际可用的LLM，但必须重新验收。

## 自动化验收范围

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q voice_tools
```

覆盖既有extract/denoise/restore接口、独立工具CLI、provider和model选择、HTTP三类接口、command协议、原文保留、零时长句处理、时间偏移、边界冲突、尾文审计、缓存恢复/失效、失败保留旧结果、人工修改保护和源路径保护。

最后一轮完整运行：72项测试全部通过，compileall通过；同窗英文术语、小数跨单位、跨窗去重接合、TTS脚本和参考音频变更均有回归测试。独立只读代码审查发现的问题已修复并复核。

HTTP与LLM/TTS自动化验证使用本地测试服务或假的模型响应；未调用真实收费服务，也未进行真实LLM分句质量或真实TTS听感验收。约102分钟全片尚未运行，实际转写错字、说话重叠、轻声漏检等仍需试听校对；本模块不自动登记到datasets_manager数据库。

## 降噪音频的上下文越界回归

同日使用outputs/20261028井上和_0-100_denoised.wav运行时，窗口0正文到24秒，含上下文的音频到24.6秒，模型将上下文尾词“も。”返回为24.24–25.04秒。旧校验把正文外的部分越界也作为整任务失败，导致无法完成。

修复后只对正文之外的上下文尾词保留原时间并审计，下一窗口独立处理正文；没有放宽正文内的严重越界、倒序和重叠检查。原始对齐现在会在校验前保存到logs/align-window-NNNNNN.json，失败也有具体窗口和时间信息。

恢复命令：

```bash
python3 -m voice_tools sentences 'outputs/20261028井上和_0-100_denoised.wav' \
  --config configs/sentences.soniqo.json \
  -o outputs/inoue_sentence_dataset --resume
```

真实运行成功导出27段，14段带复核标记，alignment_issues.json有2条记录（窗口0上下文尾词、最后窗口完全落在音频外的尾词）。前者对应文字已由下一窗口识别并保留在正文字幕中。源降噪文件SHA-256为901d96f6027b705760c7d1ef1c5c6cc2fab8355c69cfb58dcc8117f8b937d215。新增3项回归测试后，完整75项测试及compileall通过；此记录仍不代表逐句听校完成。
