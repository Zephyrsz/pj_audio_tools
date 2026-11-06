# OpenRouter ASR 验收记录

日期：2026-11-05。使用 Python 3.14.7、当前 PATH 中的 FFmpeg/ffprobe。

## 实现来源与协议

- 基础实现来源：相邻 `tts_test/tts_studio_refactored/backend/infrastructure/openrouter_asr.py`、`backend/api/asr_schemas.py` 及 `config/asr-models.json`。
- 新适配器：`voice_tools/providers/openrouter_asr.py`，通过 `Registry` 注册为 `asr/openrouter`，不引用源项目运行时。
- 默认模型：`google/gemini-3.5-transcribe`，与源项目一致；本次真实请求成功，不代表所有账户均有同等模型权限。
- `POST https://openrouter.ai/api/v1/audio/transcriptions`，JSON 请求包含 `model`、`input_audio.data`（原始音频字节的 Base64）与 `input_audio.format="wav"`，读取响应 `text`。
- 协议对照：[OpenRouter STT 官方文档](https://openrouter.ai/docs/guides/overview/multimodal/stt)。可选 `language` 使用 ISO-639-1 代码。本次未传语言提示，由模型自动识别。
- 密钥从本项目 `configs/or.cfg` 的 `OPENROUTER_API_KEY` 项读取；未复制、显示或写入结果文件。

## 用户更新后的真实输入

输入为 `/Users/rgwei/pj/pj_audio/voice_tools/outputs/inoue_sentence_dataset/dataset`，不是最初提到的 `_2` 数据集。输出为独立新目录 `/Users/rgwei/pj/pj_audio/voice_tools/outputs/inoue_sentence_dataset_openrouter_asr`。

```bash
python3 -m voice_tools.batch_asr \
  outputs/inoue_sentence_dataset/dataset \
  -o outputs/inoue_sentence_dataset_openrouter_asr
```

结果：

| 项目 | 实测值 |
| --- | --- |
| 输入 WAV | 27 |
| 过滤条件 | 文件时长 > 2 秒，无上限 |
| 选中且识别成功 | 14 |
| 跳过（≤ 2 秒） | 13 |
| 选中总时长 | 83.346 秒 |
| 最长选中音频 | 23.930 秒 |
| 导出 WAV | 14 |
| UTF-8 清单行数 | 14 |
| 检查点状态 | complete |

选中文件编号：`000001`、`000002`、`000003`、`000008`、`000009`、`000012`、`000013`、`000014`、`000016`、`000017`、`000018`、`000019`、`000021`、`000023`。

真实输出前两行：

```text
000001:東京は急に寒くなってきましたよ。
000002:はい、有楽町さんはただいま12.3度でございます。
```

## 检查

- 全部 27 个源 WAV 的当前 SHA-256 与任务开始扫描时记录一致。
- 14 个复制输出与对应源 WAV 逐字节一致，完整读取 PCM 样本通过，时长均严格大于 2 秒。
- 清单中 14 个编号与输出 WAV 编号集合相同；每条字幕非空，UTF-8 解码通过。
- 使用相邻 datasets_manager 的 `import_dataset.read_source()` 只读检查真实输出，通过 14 条音文配对校验；未向管理器数据库登记或写入数据。
- 真实执行以下恢复命令，14 条均显示 `[缓存]`，未再次识别，任务锁正常清理。

```bash
python3 -m voice_tools asr-batch \
  outputs/inoue_sentence_dataset/dataset \
  -o outputs/inoue_sentence_dataset_openrouter_asr --resume
```

自动化测试使用本地 HTTP 服务与测试 ASR 后端，不调用付费 API，覆盖 JSON/Base64 请求、文件/环境变量密钥、模型替换、CLI、HTTP 错误与重定向、非法返回、严格时长边界、音频复制、字幕格式、失败后恢复、输入/导出修改保护、重复编号、坏 WAV、任务锁和 dry-run。

最终运行 `python3 -m unittest discover -s tests -q`：91 项测试全部通过，耗时 12.915 秒。另用空 Registry 恢复真实输出，确认不创建任何模型后端即可完成，且恢复前后全部输出文件内容散列一致；实际输出中未包含密钥值。

本验收证明真实请求与数据导出链路可用；未做逐句听校，不能据此宣称字幕完全正确。输入已经是切片，脚本保留切片边界，不重新分句或补全跨片段文字。
