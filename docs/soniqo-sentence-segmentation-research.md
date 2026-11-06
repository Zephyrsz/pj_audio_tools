# Soniqo 语音断句与字幕模块调研

调研日期：2026-11-04。目标：从长录音导出语义完整的短音频及对应字幕。本文件为选型调研，不代表新增功能已经实现或目标音频已经完成识别。

## 1. 调研方法与可复核版本

- 读取 Soniqo 官网、GitHub 组织 API、speech-swift 官方文档和实际源码。
- 初期 `web.run` 的页面返回为空，通过 HTTPS `curl` 和 `git` 直接取得一手资料；主调研随后也通过 `web.run` 取得官网、Qwen官方README与固定版本源码交叉核对，不使用二手介绍填补结论。
- speech-swift 本轮 main 快照：`a57702e6f523fa36ac5532d2bdc5f35f701a052c`。
- `git ls-remote --tags` 与 fetch 独立确认：`v0.0.23` 对应 `c1aa219bc2284239ff6917d675a3e1978c840260`。本机 CLI 帮助由主调研核查，不能默认安装版具备 main 的所有新特性。
- 公开代码只克隆在临时目录 `/tmp/soniqo-research.vYLfll/speech-swift`，未构建、运行模型、下载权重或上传用户音频。
- 文中“源码确认”是静态检查；“风险推断”表示从实现推导出的失败可能，未声称在用户音频上复现。

来源：[官网](https://soniqo.audio/)、[GitHub 组织](https://github.com/soniqo)、[组织仓库 API](https://api.github.com/orgs/soniqo/repos?per_page=100)、[main 快照](https://github.com/soniqo/speech-swift/tree/a57702e6f523fa36ac5532d2bdc5f35f701a052c)、[v0.0.23](https://github.com/soniqo/speech-swift/tree/c1aa219bc2284239ff6917d675a3e1978c840260)。

## 2. 项目定位

Soniqo 是本地运行的语音模型工具链，不是单一的“语义断句算法”。组织下包含：Apple 平台 Swift/MLX/CoreML 工具集 `speech-swift`；C++17/ONNX/LiteRT 跨平台 `speech-core`；Android SDK `speech-android`；用于语音克隆创作的 `speech-studio` 等。用户的 Apple Silicon 环境及既有 `speech` CLI 对应的主要入口是 `speech-swift`。

源码确认：`speech-swift` 包清单要求 macOS 15 / iOS 18，README 标注面向 Apple Silicon。官网对 Android/Windows/Linux 的叙述是整个产品家族能力，不能解释成同一个 Swift CLI 在所有系统上具备完全相同能力。仓库代码为 Apache-2.0；各模型权重应分别查看模型卡，不能把仓库许可证推广为所有模型许可。

来源：[README](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/README.md)、[Package.swift](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Package.swift#L1-L9)、[LICENSE](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/LICENSE)。

## 3. 与本需求直接相关的能力

| 组件 | 源码/文档确认的输出 | 对本需求的用途与限制 |
|---|---|---|
| Qwen3-ASR | 字符串转写；0.6B/1.7B 多种量化版本；可传语言提示与 context | 可作为日语识别候选；普通 ASR 调用不直接输出词时间戳，也不保证一条结果就是完整句子 |
| Qwen3-ForcedAligner | 输入音频+已经有的文本，输出 `AlignedWord(text,startTime,endTime)` | 将决定好的文本边界投到时间轴；不是纠错器，也不是凭空恢复漏字的工具 |
| Silero VAD | speech start/end 事件/语音区间 | 排除长静音，提供安全候选切点；有声区间不等于语义完整句子 |
| Pyannote/FireRedVAD | 离线语音区间 | 替代 VAD 候选，不负责句子语义 |
| Smart Turn v3.2 | 最近最多8秒音频的 turn-complete 概率 | 对 VAD 停顿增加语调/韵律证据；可作为辅助分数，不能替代全篇句子规划 |
| Diarization | 匿名 speaker 标签与区间 | 多人录音可避免一条 clip 混入多人；不自动证明句子完整 |
| MOSS Transcribe Diarize | 联合生成文字、时间戳、匿名 speaker 标签 | 长篇多人备选；生成的 segment 时间戳不等于独立验证的逐词对齐 |
| WhisperASR | 原生 CoreML greedy no-timestamp 转写 | 当前 Soniqo 实现明确未做 word/timestamp-token 解码；不能把上游 Whisper 能力直接算成该 CLI 的能力 |

来源：[ASR 实现说明](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/docs/inference/qwen3-asr-inference.md)、[强制对齐说明](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/docs/inference/forced-aligner.md)、[Whisper scope](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/docs/models/whisper-asr.md#runtime-scope)、[Smart Turn](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/docs/models/smart-turn-v3.md)、[diarize 命令](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/DiarizeCommand.swift)、[MOSS](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/docs/models/moss-transcribe-diarize.md)。

## 4. CLI 实际能力与机器可读接口

### 4.1 ASR

`speech transcribe block.wav --engine qwen3 --model 1.7B --language Japanese` 是现有模型选项下的调用方式，普通结果是带日志的 `Result: ...` 文本，不是 SRT 或逐词 JSON。普通 Qwen3 分支并不会因为输入很长就自动生成完整的句级字幕。

`speech transcribe-batch chunks --engine qwen3 --model 1.7B --language Japanese --jsonl --output-dir transcripts` 可让 ASR 模型只加载一次，依次处理目录里的文件。默认 batch-size=1；此命令支持 qwen3 / parakeet，而不是 transcribe 的全部 engine。默认文件扩展名 `wav,flac,mp3`，若要扫描其他类型需要显式指定 `--extensions`。

JSONL 行含 `file,text,time,rtf,duration,batch_time,batch_rtf,batch_size`。这些 time/RTF 是计算耗时与吞吐信息；**不是单词在原录音中的开始/结束时间**。主程序仍需保存 chunk 的原始 offset。源码中模型加载/预热/最终统计仍打印 stdout，所以不能把整个 stdout 直接当纯 JSONL 文件解析；只解析已校验的 JSON 对象行，并保留日志。每文件 txt 可以减少对 stdout 文本格式的依赖。

来源：[batch 参数与加载](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/TranscribeBatchCommand.swift#L12-L90)、[JSONL 字段](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/TranscribeBatchCommand.swift#L130-L180)、[transcribe 普通与流式分支](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/TranscribeCommand.swift#L365-L425)。

### 4.2 VAD

`speech vad-stream block.wav --engine coreml --json` 使用 Silero；`--engine mlx` 是其另一后端。`speech vad` 则是 Pyannote 默认 / FireRed 可选，不能把两个命令的 engine 名称混用。JSON 为区间数组，每项 `start,end,duration`，仍伴随加载日志。Silero 默认 min-silence=0.1s，Pyannote 默认约0.067s；这些是模型检测参数，不是“句子之间应有多长停顿”的语义规则。

当前 main 还支持 `speech vad-stream block.wav --smart-turn`，在确认停顿后再判断轮次是否完成。Smart Turn 文档列出含日语在内的23语言；其训练目标是会话轮次结束，不是任意离线节目的句法完整性。本机安装版本是否支持该 flag 必须以其 help 为准。

来源：[vad-stream 实现](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/VadStreamCommand.swift)、[vad 实现](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/VadCommand.swift)、[默认参数](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/SpeechVAD/Configuration.swift#L80-L109)。

### 4.3 对齐

`speech align block.wav --text '音频对应的日语原文。' --language Japanese --engine mlx` 可对已有转写对齐。省略 `--text` 会再调用 Qwen ASR；若前面已经识别过，应该传已缓存文本，避免重做与文字不一致。`--model` 控制省略文本时的 ASR 模型，`--aligner-model` 才控制对齐模型，二者不是同一参数。

align 默认 MLX 4bit 对齐器，可选 CoreML。输出是 `[0.12s - 0.45s] 文本` 行，保留两位小数，**没有 `--json` 参数**；加载和进度日志也在 stdout。频繁逐句启子进程会重复加载模型，生产用可考虑一个小型持久 Swift worker 暴露 JSONL；这属于后续模块设计，而不是当前已有接口。

来源：[完整 align CLI](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/AlignCommand.swift#L12-L31)、[模型选择/默认语言/输出](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/AlignCommand.swift#L86-L128)。

## 5. 日语与长音频的实质风险

### 5.1 日语语言提示不能省略

ASR 的语言可自动识别，但 align 默认 `language ?? "English"`。日语应显式 `--language Japanese`。Soniqo 对齐预处理用 Apple `NLTokenizer(.japanese)` 做词/形态素级分割，模型输入去除标点，但 surface 保留相邻标点供字幕重建。对齐输出单元不等于自然句子，不能按空格拆日语或按每行直接生成 clip。

来源：[默认语言](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/AlignCommand.swift#L86-L116)、[日语分词](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/Qwen3ASR/TextPreprocessing.swift#L96-L150)。

### 5.2 alignLong 的日语重分块存在静态可见的不一致

**源码确认：** 当前 main 的 `alignLong` 在检测到时间戳饱和/平台后，按已经对齐的 `plateauStart` 个“词”保留前缀；但提取剩余文字用 `remainingText.split(separator: " ")`。日语真实词数来自 NLTokenizer，和空格分段数不一致。

**风险推断：** 无空格日语一旦触发该重分块分支，`plateauStart < wordsAll.count` 可能不成立，函数 break 只返回前缀；不能相信“有 alignLong 所以任意长日语不会丢尾”。该风险在 `v0.0.23` 同样存在。main 相比该 tag 改进了饱和检查（main 超过20秒检查，tag≤240秒跳过），但没有修复剩余文本按空格切的问题。代码还设有最多10轮、剩余不足5秒直接停止等防死循环分支。

**工程建议：** 在应用层主动切短工作块、传入该块的精确文字，逐块对齐，验证文字覆盖与时间戳范围。不能把约102分钟整段一次交给 alignLong，也不能只检查进程退出码。

来源：[main 风险分支，含5秒尾段与10轮限制](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/Qwen3ASR/ForcedAligner.swift#L168-L195)、[v0.0.23 对应实现](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/Qwen3ASR/ForcedAligner.swift#L110-L190)、[main 检测阈值](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/Qwen3ASR/ForcedAligner.swift#L111-L165)。

### 5.3 时间戳精度与 CoreML 固定形状

对齐模型分类头默认5000格、每格80ms。这是输出时间分辨率，**不是每个词保证误差≤80ms**。文档观察约270秒后可能饱和，但该数字来自特定素材，不是通用可靠性保证。CoreML 对齐器的文本解码器固定 T=768；prompt 超限会抛错，API并未提供与MLX相同的alignLong入口。不要按官网“长音频”标签就选任意长度大块。

来源：[模型说明与长音频 handling](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/docs/inference/forced-aligner.md)、[CoreML 输入检查](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/Qwen3ASR/CoreMLForcedAligner.swift#L162-L213)。

### 5.4 StreamingASR 的 FINAL 不是语义完整保证

`speech transcribe ... --stream --max-segment 10` 使用Silero区间和最大长度安全切分。FINAL仅表示这次ASR区间已经结束；源码明确达到最大区间时可以强制切开，可能在一句话中间。它的 start/end 来自 VAD 区间，不是单词精确位置。快速原型可用，但还需要跨区间合并/语义规划/词级对齐。

来源：[StreamingASR 配置与强制切分](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/Qwen3ASR/StreamingASR.swift#L27-L65)、[force-split 分支](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/Qwen3ASR/StreamingASR.swift#L175-L237)。

## 6. 不能误读的 HTTP 字幕支持

**源码确认：** 当前 `/v1/audio/transcriptions` 支持 OpenAI 风格的 `response_format`，但 `verbose_json` 仅构造一条 `[0,duration]` 的全长 segment；`avg_logprob,compression_ratio,no_speech_prob` 为固定0，不是真实置信度。`srt` 和 `vtt` formatter也只输出一条从0到总时长、内容为全部转写的 cue。

**结论：** 返回合法SRT格式不代表已经按句对齐。不能直接以该接口作为“完成句子短音频+字幕”的现成实现，也不能把占位0用作自动质检的模型置信度。

来源：[verbose_json 精确实现](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioServer/OpenAITranscriptions.swift#L109-L147)、[SRT/VTT 单cue formatter](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioServer/OpenAITranscriptions.swift#L217-L222)。

## 7. MOSS 与 Smart Turn 的备选价值

MOSS MLX 后端提供131072 combined prompt/output tokens，并把多段30秒编码后的特征拼成全局上下文；CoreML后端只有1024固定tokens。文档的90分钟能力不是本机保证，实际受音频token数、输出token数、KV精度和统一内存影响。CLI MLX默认输出上限5120 tokens、CoreML默认512；达到上限时会打印Warning，并不等于给长录音完整转写。目标素材约102分钟，不宜未经验证直接整段依赖该模式。

MOSS日语属于文档列举的上游语言证据，但Soniqo自己的量化质量对比主要是英语；不能拿英语WER/DER宣称日语素材效果。其speaker标签是文件内匿名ID，不是身份识别，也不是自动提取指定真人的保证。

Smart Turn较轻，可增加边界韵律证据；文档输入只看最后8秒，连续静音达到2秒仍强制结束，失败时按complete处理。这是实时交互取舍，离线高质量切句应把它作为建议分数，不能设为唯一裁判。

来源：[MOSS 模型/语言/上下文说明](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/docs/models/moss-transcribe-diarize.md)、[CLI生成上限与stop warning](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/TranscribeCommand.swift#L258-L361)、[Smart Turn行为](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/docs/models/smart-turn-v3.md#use-in-streamingvadprocessor)。

## 8. 适合本项目的初步技术结论

建议把Soniqo当作可替换的识别/VAD/对齐后端，Python模块负责长录音任务编排、原时间轴、句子决策与文件导出。推荐分工：

1. VAD 提供有声区间与停顿候选，不把每段强制认定为一句。
2. 合并成带上下文的短ASR工作块；工作块允许重叠，但最终必须做文字/时间去重与offset回填。
3. ASR 保留原语言逐字稿与标点；LLM如参与，只返回相邻文本单元的分组/边界，不生成任意时间戳、不改写原文、不翻译原文替代字幕。
4. 对有准确音频范围与对应文本的短块做forced alignment，映射词/形态素到原录音时间；句子规划和对齐的先后可灵活，但最终切点必须来自已校验的音频时间轴。
5. 用停顿和局部能量校正句首/句末，保留适度语音边缘余量，避免只按80ms网格硬切辅音或把邻句吞入。
6. 输出每句WAV/FLAC、同名txt或clip-local SRT、全局SRT与manifest；manifest必须同时保存源时间与clip内字幕时间、模型/版本、工作块offset和人工复核标志。
7. 强制校验对齐覆盖、越界、零长度/重叠、重复文字、空音频、可疑长句/极短句；可疑项进入复核，不把“进程成功”当“语义完整且准确”。

以上为工程建议，不是宣称目前已有命令可以一键完成。具体工作块时长、短句长度和padding应通过目标素材的代表性样本标注/试听后确定。

## 9. 本轮尚未验证

- 未试听或识别目标文件，文件名不能证明语言、声道用途、人数、配乐程度。
- 未在目标录音测量任何模型的日语CER、时间戳误差、句子完整率或运行耗时。
- 未证明MOSS/Smart Turn在本机安装版本可用；当前main有能力不等于Homebrew安装包已经发布。
- 未运行端到端切句；本文件不应作为模型质量验收记录。

## 10. 本机与目标文件检查

以下为2026-11-04本机只读检查，不是语音模型识别结果：

| 检查项 | 结果 |
|---|---|
| 原文件 | `/Users/rgwei/pj/pj_audio/voice_tools/raw/20261028井上和.m4a` |
| 音频流/容器时长 | 6126.724354秒，即1小时42分6.724秒 |
| 编码/采样率/声道 | AAC-LC / 44100Hz / 双声道 |
| 文件大小 | 99084733字节 |
| 文件语言标签 | `und`，不能据此确认日语 |
| 本机 | Apple M5 Max / 36GB统一内存 / macOS 26.6.2 / arm64 |
| 安装包 | `brew list --versions speech` 返回 `speech 0.0.23` |
| 缓存 | 发现Silero MLX模型目录；未发现Qwen ASR/ForcedAligner的Hugging Face hub缓存目录，不据此断言其他位置没有权重 |

确认本机帮助包含 `transcribe-batch --jsonl`、`align --text --language --engine`、`vad-stream --json`；`align`没有JSON选项。本机 `speech --version` 不支持，版本从Homebrew安装记录确认。没有运行全片ASR/VAD/对齐、没有下载新模型。

## 11. Qwen上游与封装的区别

Qwen官方仓库快照：`7c6daf77a2421100f5fb066495372c00129d39ff`。官方语言表确认Qwen3-ASR与Qwen3-ForcedAligner都支持Japanese。ASR返回文字，若需要时间戳，官方工具通过额外forced aligner生成；因此“ASR+时间戳”可能本来就是两模型流程，不意味着单个ASR推理直接输出精确边界。

官方模型说明称强制对齐可处理最多5分钟语音；该快照Python包装器却将带时间戳转写的工作块目标设为180秒（不带时间戳为1200秒），并在低能量处寻找边界。这个值是具体包装器的实现参数，不是Soniqo CLI的承诺，也不是本模块的最佳工作块时长。官方Python日语分词使用nagisa，Soniqo使用Apple NLTokenizer；分词差异应纳入结果对比，不能未经测试宣称某个移植必然更好或更差。

来源：[官方语言与时间戳使用说明](https://github.com/QwenLM/Qwen3-ASR/blob/7c6daf77a2421100f5fb066495372c00129d39ff/README.md)、[Python工作块参数与边界搜索](https://github.com/QwenLM/Qwen3-ASR/blob/7c6daf77a2421100f5fb066495372c00129d39ff/qwen_asr/inference/utils.py#L34-L36)、[转写与对齐编排](https://github.com/QwenLM/Qwen3-ASR/blob/7c6daf77a2421100f5fb066495372c00129d39ff/qwen_asr/inference/qwen3_asr.py#L366-L424)、[日语分词实现](https://github.com/QwenLM/Qwen3-ASR/blob/7c6daf77a2421100f5fb066495372c00129d39ff/qwen_asr/inference/qwen3_forced_aligner.py#L101-L109)。

## 12. 推荐第一版模块设计（建议，尚未实现）

### 12.1 流程与参数起点

原录音 → 分析音轨 → VAD/工作窗口 → ASR → 局部强制对齐 → 全局文本单位序列 → 规则分句/可选LLM → 声学边界修正 → 原录音切片及字幕。

1. **保留两条用途不同的音轨。** 模型分析可用16kHz mono PCM16 WAV；导出从原录音或保持原时间轴的高质量解码母轨取样。先确认双声道是同一信号还是不同人声，不能机械只取左声道或默认混音。默认使用原录音识别；降噪是否提高识别质量先做A/B。既有Sidon记录存在逐窗时间缩短，不能把restore输出时间直接应用到原录音，见[已有源码与实测记录](sidon-long-audio-research.md)。
2. **VAD只辅助调度。** 把相邻有声区间合成工作窗口，先试20–30秒正文和两侧0.5–1秒上下文，在附近安全停顿落窗口边界；这些是试验初值，不是模型硬限制或已经验证的最优参数。采用保守VAD阈值，低能量/疑似漏检段保留复核机会，保留原录音和被跳过区间的记录以便找回漏检内容。
3. **ASR与对齐分阶段缓存。** 先比较Qwen3-ASR 0.6B和1.7B在相同代表性样本的文字质量与本机吞吐，再定默认。若确认日语，显式传Japanese。已有文字传给align，避免重复ASR。每块保存真实原始起点offset；局部时间加offset回原时间轴。
4. **跨工作窗口恢复同一句。** 工作块不是最终句子。对重叠区在时间和文字一致性基础上去重，并为每块指定正文归属区间，不能仅按重复字符串删除（真实讲话也会重复词句）。窗口边缘截词或两块识别冲突时局部重试或标记复核。合并后再按全局文字单位规划句子，允许一句跨多个识别窗口。
5. **先规则，后可选语义判断。** 保留原文及标点，给字/词/形态素稳定ID，并维护到原文字串的映射；标点没有独立音长，不强迫对齐器给它时间。明确句号、问号、叹号作为候选，结合上下文、停顿、说话人变化判断。日语的「けど」「ので」「と思って」等不能仅因有停顿就判定已经完句；口语也可能故意以接续表达收尾，需上下文而非绝对禁词表。
6. **LLM只提边界。** 给有ID的原文单位及前后文，要求仅输出各句的结束ID和复核标记。程序验证每个单位按序完整覆盖、无重复、无遗漏；不让模型改写原话、补成书面语、翻译后替代原文或生成浮点时间戳。语音中本来没说完的句子应标记incomplete，而不是强行生成完整句。若确需纠正识别文字，另存修订并对修订范围重新对齐。
7. **只在安全声学区域修切点。** 用首末已对齐单位定语音范围，再结合局部VAD/能量在邻近非语音间隙寻找切点。初试前留80–150ms、后留150–250ms，受相邻讲话和文件首尾约束，不机械吞入下一句。没有安全间隙时保留对齐边界并标记风险，不为了吸附静音移动到别的词。目标片长可先取2–20秒、30秒为软告警：超长完整句保留或复核，不能为达长度指标硬切半句；简短完整回应也不强行合并。

### 12.2 接口与输出建议

建议新增命令名 `sentences`（当前尚不存在）。模块接口至少分为 `transcribe`、`align`、`segment`、`export` 四个可缓存阶段；重新断句只重跑segment/export。模型适配器与句子规划器分离，使Soniqo后端以后能换为其他本地引擎。

输出布局建议：

```text
outputs/<任务名>/
  transcript_utf8.txt       # 最终每句字幕，兼容datasets_manager现有导入器
  logs/process.jsonl        # 输入登记、处理阶段、模型参数、状态与错误
  transcript.raw.json       # 原始识别结果与窗口offset
  alignment.json           # 统一单位ID、原文字串映射、全局时间
  sentences.json           # 分组、语音范围、实际切片范围、复核标记
  subtitles.srt            # 原录音全局时间轴
  manifest.json            # 输入指纹、版本、参数、每句路径与时间
  wav/
    000001.wav
    000001.txt
    000001.srt             # 片段内相对时间轴
```

manifest分别保存`speech_start/end`与`cut_start/end`。片段字幕时刻=`原录音字幕时刻-cut_start`，而不是直接复用全片时间；相对字幕必须在实际片段范围内。句子音频、字幕cue、屏幕换行是三种结构：一句音频可以有多条显示cue，不能为屏幕一行字幕拆坏完整句。

ASR可先复用`transcribe-batch`，保留日志并解析经过schema校验的JSON行；align第一版可按固定版本文本格式封装并严格校验，若模型反复加载成为瓶颈，再做单模型常驻的Swift JSONL worker。批量导出可复用现有`extract_clip`的源文件保护与重编码策略；全片大量片段时需要评估反复解码/ffprobe开销，再决定一次解码母轨或批量导出。所有新命令都应明确失败/取消行为，不覆盖源文件或未经允许覆盖已有任务。

### 12.3 验收方案

先取开头、中段、结尾以及停顿少/轻声/噪声重片段，合计5–10分钟做人工参考；不能只选开头最清楚的一段。对同样本比较：VAD区间直接切、标点+对齐、可选LLM分句三种结果。分别记录原文错误、遗漏/重复、句子完整率、边缘截字/吞邻句、时间误差与处理耗时。日语可用字符错误率CER，但听感和语义完整性仍要试听标注。

自动硬校验包括：文本单位覆盖与顺序、时间有限且单调、`0<=start<end<=source_duration`、局部/全局offset一致、实际音频样本数、字幕在clip范围内、输出可完整解码。疑似重叠讲话、未对齐单位、异常零时长、同时间戳长平台、很长或很短片段进入复核清单。padding造成的语音空白重叠和真正重复收录讲话应区分处理。

本轮仅完成研究和模块建议，尚未执行以上对比实验，因此不能承诺目标文件的最终质量、片段数量、准确率或完成时长。

## 13. 单音频任务与现有数据集管理兼容（2026-11-05修订）

2026-11-05后续需求补充：管理器已新增「工作空间 → 音频注册」，可单独登记原始长音频（`source`）或语音克隆参考音频（`reference`），每条只能选一种用途。该功能使用独立`audio_assets`表与`/api/audios`接口保存本机文件路径和音频信息；参考音频可选保存说话人、语言和对应文本。后续处理程序可通过音频ID取得输入，或继续直接使用本地路径。它不承接处理进度、ASR/对齐结果或任务日志；以下单任务日志及最终数据集导入约定仍适用。此条补充取代此前完全不需要音源登记的范围判断，未将自动处理或TTS推理纳入管理器。

一次任务仍处理一个输入音频，输入可来自管理器单音频登记或直接指定本机路径，处理过程在本项目本地记录；最终逐句音频与文本登记到datasets_manager。不采用生成批次表、来源关系表或管理器内处理工作流。最终结果使用可安全重试的CLI和HTTP登记入口。

### 13.1 现有兼容结论

已核查`/Users/rgwei/pj/pj_data/datasets_manager/import_dataset.py`：现有导入器可以登记包含WAV的目录，并读取UTF-8/UTF-8 BOM的`标识:字幕`清单。最终产物按该格式输出；管理器沿用数据库结构和界面。新增的`register_result.py`与`POST /api/datasets/register`共享校验和登记逻辑，保护人工字幕，避免重试时隐式重导。

- 音频默认放在数据集根目录下的`wav/`，使用唯一文件名，如`000001.wav`。
- 同根目录的`transcript_utf8.txt`中，每行是`不含扩展名的文件名:字幕`，例如`000001:これは検証用の字幕です。`。该例仅说明格式，不是用户录音的转写。
- 每个WAV都必须有且仅有一条非空字幕，清单也不能包含没有对应WAV的条目；字幕写成单行，SRT显示换行不直接写入此清单。
- 字幕内部可以有英文冒号，现有解析器只按第一个冒号分隔。输出文件名采用数字序号，避免冒号和路径分隔符。
- SRT、TXT、日志和JSON可以作为附属文件保留；它们不参与现有WAV扫描，也不是管理器导入的必要输入。

### 13.2 本地登记与处理记录

输入音频的绝对路径、大小、时长和可选内容指纹作为本地任务的输入记录；不要求先取得管理器的source_id。每次任务有独立输出目录，避免不同处理结果相互覆盖。

`logs/process.jsonl`按事件记录时间、阶段、开始/完成/失败、所用模型与参数、耗时、错误以及登记返回信息。原始转写、逐词对齐与句子时间映射继续保存为本地JSON，便于查错与重做分句，不写入管理器数据库。`manifest.json`若保留，也只是本地结果索引；管理器不需要读取它。

推荐执行顺序：本地记录单个输入 → VAD/ASR/对齐/分句 → 完整导出PCM WAV与字幕清单 → 调用结果登记入口校验并登记 → 本地记录dataset_id或失败信息。

### 13.3 使用结果登记入口

自动处理模块最终调用`POST /api/datasets/register`，配置字段为`name`、`storage_dir`、`audio_subdir="wav"`、`transcript_path="transcript_utf8.txt"`。后两个字段可以省略为上述默认值，字幕路径不可为NULL或空白。路径均由管理器后端机器解析，根目录必须为绝对路径。也可通过标准库CLI登记，无需启动Web服务。

命令行示意（切句模块尚未实现，此命令不表示下述数据集目录已生成）：

```bash
python3 /Users/rgwei/pj/pj_data/datasets_manager/register_result.py \
  --name '<唯一数据集名称>' \
  --root '/绝对路径/最终数据集目录' \
  --audio-subdir wav \
  --transcript transcript_utf8.txt
```

HTTP首次成功返回201，JSON为`{status,dataset,import_result}`，其中`status="created"`；相同登记重试返回200、`status="existing"`。CLI成功输出包含`dataset_id`、`registration_status`和记录计数的JSON，失败非零退出并把错误写入stderr。自定义管理器数据库时，CLI的`--db`需与后端`DATASETS_DB`一致。

生成文件已完成而管理器暂时不可用，或登记响应丢失时，保留输出和本地日志，之后只重试登记，不重新运行ASR。重复登记在同一SQLite写事务内检查配置和完整音频文件名集合，复用数据集与样本ID，不覆盖数据库中人工修改或清空的字幕。名称/目录/配置/文件集合冲突返回409；非法文件400，请求字段非法422。坏WAV、空音频、截断音频、音文不匹配和越出目录的文件会被拒绝，不产生部分登记。WAV须为PCM，字幕位于根目录内，音频位于指定音频目录内。

相同登记只按规范化配置和文件名集合识别，不比较内容指纹；输出发布后保持文件不变，不同版本使用新目录和新名称。原`POST /api/datasets`仍对重复名称或目录返回409；原`import_dataset.py`与sync仍会以源字幕覆盖数据库字幕，因此自动流程不调用它们重试。成功后本地保存dataset_id，并可通过现有列表、明细、字幕编辑和音频试听管理结果。

### 13.4 已执行的兼容性验证与边界

2026-11-05用临时目录、临时SQLite和两个合成的0.25秒/16kHz/PCM16 WAV调用了未修改的现有导入器：

- 两条日文字幕均正确写入，包含`12:34`的文本也完整保留。
- 同目录中的附属SRT以及数据集根目录的JSONL日志不影响导入。
- 第一次导入新增2条；相同清单再次执行新增0条、更新0条，复用原dataset_id和样本ID。这个结果只验证内容不变时的重复执行，不代表会保护人工修改后的字幕。
- 正式数据库、管理器schema和运行代码的散列均未改变。临时文件已清理，没有将测试样本登记到真实数据集库。

用户确认扩展后，新增结果登记测试使用临时PCM WAV、字幕和独立SQLite，覆盖CLI/HTTP首次登记、人工改字或清空后重复登记、并发重试、配置/文件集合冲突、非法文件/路径拒绝，以及既有明细查询和Range音频试听。数据库schema无需变化；正式数据库不参与这些测试。

本轮实现的是管理器结果登记能力。尚未运行用户长音频的真实切句，也未生成该录音的数据集；voice_tools的自动单音频处理与最终调用仍按第12节后续实施。
