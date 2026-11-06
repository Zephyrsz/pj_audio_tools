# Sidon 长音频处理：源码核查与优化建议

核查日期：2026-11-04。范围：官方 `soniqo/speech-swift` 源码；没有运行模型、安装依赖或修改功能代码。

## 版本与结论

本机 Homebrew `speech 0.0.23` 的配方指向官方 `v0.0.23` 源码包，tag 对应提交 **`c1aa219bc2284239ff6917d675a3e1978c840260`**（2026-07-17）。以下默认分析该固定版本；另对比当前 main **`a57702e6f523fa36ac5532d2bdc5f35f701a052c`**（2026-10-30），不把新版能力当成本机已具备的能力。[版本来源](https://api.github.com/repos/soniqo/speech-swift/git/ref/tags/v0.0.23) · [提交](https://github.com/soniqo/speech-swift/commit/c1aa219bc2284239ff6917d675a3e1978c840260)

**约 102 分钟输入在窗口数量上可处理，但当前实现不是流式处理，也不保持原始时间轴。** 对只需独立试听的清理音轨，可先按现有命令处理；若要替换视频原声、沿用原始字幕或精确比较时间点，应先解决窗口拼接的时间轴问题。36 GiB 内存并不意味着必须切小文件；主要优化应先看声道选择、解码缓冲及计算设备，而不是增加模型并发。

## 实际执行路径

- `RestoreCommand.run()` 先将整文件解码并重采样为 16 kHz 的 `[Float]`，再加载一次模型、调用一次 `restore()`，最后完整写出 WAV。没有 `batch-size`、窗口长度、流式写出或恢复进度参数；其计时从模型加载之后开始，不包括解码和模型加载。[CLI L17–27、39–74](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCLILib/RestoreCommand.swift#L17-L74)
- `restore()` 按 **160000 个输入采样点，即 10 秒**串行循环，每次分配一个 chunk；同一 predictor/vocoder 实例复用，输出追加到完整 `[Float]`。模型输入是 `[1,499,160]`，逐窗同步 `prediction(from:)`，当前封装没有批量或并行预测。[恢复循环与模型输入](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechRestoration/SpeechRestoration.swift#L161-L240) · [模型调用](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechRestoration/SidonModels.swift#L19-L58)
- 输入不是仅限 WAV：实现交给 `AVAudioFile(forReading:)`，没有扩展名白名单，具体 M4A 编码能否读取取决于系统解码器及实际文件。**0.0.23 所谓 mono 是只取第一声道，不是左右声道平均**；对右声道有人声的录音可能丢失内容。不能直接沿用新版文档的混音行为。[加载器 L26–50](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/AudioFileLoader.swift#L26-L50)

## “裁回原时长”与真实漂移

配置固定：16 kHz 输入、48 kHz 输出、每窗 160000 输入点、**479014 输出点**，即每满窗只有 **9.979458333 秒**，比 10 秒少 **986 点／20.541667 ms**。[配置 L63–75](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechRestoration/Configuration.swift#L63-L75)

源码的精确长度规则（`n` 为重采样后实际输入采样点数）是：

```text
windows = ceil(n / 160000)
raw = windows * 479014
target = round(n * 3)
output = min(raw, target)
```

随后只是拼接全部输出，若超过 `output` 才删尾部；**不会补齐短输出、逐窗回填原时间轴、重采样补偿、重叠或 crossfade**。官方文档“trim to original duration”实际只保证不超过输入时长，不保证相等；官方单测也明确 10 秒和 20 秒输入保留短于目标的 raw 长度。[窗口规划 L132–152](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechRestoration/SpeechRestoration.swift#L132-L152) · [拼接及裁尾 L172–190](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechRestoration/SpeechRestoration.swift#L172-L190) · [单测 L32–75](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Tests/SpeechRestorationTests/SpeechRestorerLogicTests.swift#L32-L75)

本次会话已提供的 100 秒实测输出为 4790140 点／99.794583333 秒，恰好是 10 个完整窗口，符合以上规则。对于待处理 M4A 的容器时长 **6126.724354 秒**，预计需 **613 窗**；若解码后的长度也落在该窗口数内，raw 为 **293635582 点／6117.407958333 秒**，约短 **9.316396 秒**。精确差值应以解码后 `n` 为准，不应只由容器时长断言。

**尾部总时长差会掩盖内部漂移。** 第 613 窗原始起点是 6120 秒，直接拼接时起点却是 6107.4285 秒，累计提前 **12.5715 秒**；最后一窗的补零输出又填回一部分尾部长度，所以不能把最终“只短约 9.3 秒”解释为全片同步误差不超过 9.3 秒。这是由拼接位置推导出的窗口起点差，不是对窗内语音延迟的实测。

简单处理的边界：

- **外部分成几分钟再各跑 CLI**：每个内部 10 秒窗仍少 986 点；只限制内存／便于重试，还增加模型加载次数和分段边界，不能自动修复同步。
- **只在最终补静音**：仅修正文件总长，内部窗口仍然提前。每个 10 秒槽末尾单独补 986 点能阻止累计提前，但可能每 10 秒产生约 20 ms 缺口／点击，也没有恢复边界内容。
- **整片拉伸到原时长**：把全部位置按同一比例移动，无法恢复每窗边界的局部缺口、上下文或瞬态连续性；总长正确不证明局部同步正确。不能仅凭长度推断短缺发生在每窗的哪一端，也不能证明音频内容整体匀速压缩。

## 内存、吞吐与新版差异

0.0.23 为整文件分配原采样率多声道 PCM buffer，再复制第一声道；重采样又分配完整 source/target buffer 和输出数组，恢复阶段保留完整输入及完整输出，写 WAV 时再创建完整 PCM16 `Data`。因此神经网络虽只看 10 秒窗，端到端内存仍随总时长增长。[解码](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/AudioFileLoader.swift#L26-L50) · [重采样缓冲](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/AudioFileLoader.swift#L196-L225) · [转换输出缓冲](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/AudioFileLoader.swift#L298-L359) · [WAV 写出](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/WAVWriter.swift#L11-L46)

按 6126.724354 秒、44.1 kHz stereo 估算：原始 Float32 stereo buffer 约 **2.013 GiB**；第一声道副本约 **1.007 GiB**；16 kHz Float32 输入约 **0.365 GiB**；613 窗的 48 kHz Float32 输出约 **1.094 GiB**；PCM16 WAV `Data` 约 **0.547 GiB**。这些是单项数据量，生命周期并不完全重叠，不能相加声称实际峰值；模型、转换缓存及系统压力另计。36 GiB 下具有余量的可能性很高，但应以本机峰值 RSS／内存压力验证，不凭文件的约 99 MB 压缩大小估算。

0.0.23 默认两模型均 `.all`，但**已支持** `SPEECH_COREML_COMPUTE_UNITS=gpu` 环境变量（统一覆盖两个模型）；无需不存在的 `--compute-units` 参数。[旧版设备解析](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/CoreMLComputeUnits.swift#L23-L46) · [加载器应用覆盖](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/CoreMLLoader.swift#L64-L78)

官方后续 **2026-08-30** 提交把 predictor 默认保留 `.all`、vocoder 改为 `.cpuAndGPU`，原因是在其 M5 Pro／macOS 26.5 测试中旧默认触发长时间 ANE 编译失败及慢速回退。该提交还新增 CLI `--compute-units`。这是值得比较 GPU 的证据，**不是本机可直接套用的速度保证**；int8 的主要收益是 predictor 体积和内存降低，也不等于更快。[设备放置修复提交及测量条件](https://github.com/soniqo/speech-swift/commit/b066a0624e3aa4cedf1e30f3348d2e26d8c85d29) · [旧版变体与峰值说明](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/docs/models/sidon.md#L69-L77)

main 新增有界内存 `AudioFileStream`，并将一般加载器默认改为 `mixAll`；**main 的 restore 命令仍调用完整 `load()`，恢复窗口／拼接／min 裁尾逻辑也与 0.0.23 相同**，因此仅升级不能推断长音频内存与同步问题已经解决。[main 流式 reader](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCommon/AudioFileStream.swift#L36-L82) · [main 加载器](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCommon/AudioFileLoader.swift#L23-L87) · [main restore 命令](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/AudioCLILib/RestoreCommand.swift#L61-L89) · [main 拼接逻辑](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/SpeechRestoration/SpeechRestoration.swift#L166-L224)

## 本次会话的实测与边界

以下由主任务的独立实测提供，本研究未重复运行：

| 输入／测试 | 已观察结果 | 可说明的范围 |
|---|---|---|
| 原 M4A 的 20 秒 AAC 截段，直接交给现有适配器 | 成功；总墙钟 4.574 秒；输出 19.958917 秒 | 证明该编码的短 M4A 路径可用，未证明整片稳定性 |
| 模型已缓存，约 100 秒 16 kHz mono Float32 WAV，fp16 | 总墙钟 5.270 秒；CLI 的恢复阶段 0.99 秒；峰值 RSS 1326579712 字节（约 1.235 GiB）；输出 4790140 点 | 热运行吞吐较快；不把该输入峰值当作原始 stereo M4A 的整片峰值 |

第二项输入经 ffprobe 显示 1600000 点／100 秒，但 CLI 日志显示加载 1599465 点／99.9665625 秒。另用两份精确 20 秒／16 kHz 的 WAV 比较：PCM16 读入 **320000 点**，Float32 读入 **319465 点**；两者仍各需两个窗口，均输出 958028 点。说明本机安装版本存在可重复的 Float32 WAV 读入长度差异，原因未证实，不能把它归因于模型窗口短缺或直接归咎于 AVAudioFile。中间文件优先选择 **16 kHz mono PCM16 WAV**，并核对读入采样数。以上速度只支持“本机初步看来可在分钟量级完成”的预期，不能给出整片必然耗时。

## 推荐方案与验收条件

1. **当前即可实施的保守方案**：先确认录音左右声道哪一路包含所需人声；如需混音，明确生成 **16 kHz mono PCM16 WAV** 中间文件，避免旧版悄悄只取左声道，并降低原始高采样率缓冲峰值。保持单进程、单模型实例串行执行，比较同一短片默认设备与 `SPEECH_COREML_COMPUTE_UNITS=gpu` 的冷加载及热窗速度，用本机结果估算整片时间。保留原 M4A 与原时间轴用途，恢复结果先视为独立试听音轨。
2. **若只优化可靠性／内存**：后续增加有界解码→窗口累积→复用模型→增量 WAV 写出的流水线，附带窗口进度、取消和断点记录；复用 main 的 reader 思路。外部分钟级分片可作为临时可重试策略，但应说明同步缺陷仍在，且不默认多进程并发。
3. **若必须保持时间轴**：需要专门实现“重叠上下文＋校准延迟＋取窗口中央有效区域＋按原采样位置写回”，在真正重叠的区域做 crossfade／overlap-add，最后精确裁剪目标样本数。固定 10 秒图可用小于 10 秒的步长换取前后上下文；必须先实测窗内延迟和有效边界，再确定步长、裁切位置与交叉淡化宽度。crossfade 本身只处理接缝，不负责补回时长或确定同步。
4. **验收不是只看总时长**：至少检查起点、中段、末段以及多个 10 秒边界附近的人声事件与原轨是否对齐；检查辅音／呼吸／书写重叠处有没有断裂、重复或重建失真。短片成功、固定长度单测、官方单片速度都不能代替 102 分钟连续运行与局部同步验证。

尚未证实：本机 full-file 峰值内存、全长稳定性、GPU 相对收益、原始 M4A 解码后的精确 `n`、窗内语音的实际延迟／边缘损失位置及可接受的 crossfade 参数。这里未把研究建议写成新功能或质量保证。

## 补充：DeepFilterNet3 `denoise` 能否处理长音频

**可以走默认长音频路径；它与上述 Sidon 固定短窗输出的问题不同。** 本节仍以 `v0.0.23` 固定提交为准。上游命令默认文件名是 `_clean.wav`，本项目适配器的 `_denoised.wav` 只是输出命名约定，不代表另一套模型。视频应先提取音轨，再将音频交给降噪路径。[上游命令及默认文件名](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCLILib/DenoiseCommand.swift#L6-L28) · [命令读取与写出](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCLILib/DenoiseCommand.swift#L32-L87)

### 分块阈值、长度与接缝

- 默认 `chunkSeconds=45.0`、`overlapMs=500`，**超过 45 秒即进入分块**；“约 60 秒”是 Core ML 单次预测的 `RangeDim(1,6000)` 时间轴上限，并不是默认分块阈值。长音频保持默认分块，**不要加 `--no-chunk`**；也不应把窗口调到超过模型上限。输入较短时走原有单次路径。[CLI 参数／分支](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCLILib/DenoiseCommand.swift#L21-L65) · [模型上限说明](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechEnhancement/SpeechEnhancement.swift#L71-L100)
- 它不是机械切成“45 秒＋最后碎尾”：先计算块数，再等分窗口以避免很短的末块，按原输入采样索引及重叠长度推进。同一模型实例复用；STFT 和 EMA 归一化状态跨块保留，Core ML 内的 GRU 状态每次预测重置，500 ms crossfade 用于缓解块首收敛差异。**这里的 EMA 特征归一化不等于拼接权重归一化。**[分块状态与规划](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechEnhancement/SpeechEnhancement.swift#L226-L303)
- 每个 `enhanceCore()` 先在尾部补 **960 点**，STFT/iSTFT 后去掉 **480 点**算法延迟，再按该块的 `samples.count` 截取；默认 FFT=960、hop=480。这条路径的设计保留该块输入样本数，不是 Sidon 的“每个 10 秒固定只输出 479014 点”。拼接抵扣输入重叠，末尾只删除超长部分。对这份约 6126.724354 秒输入，若 48 kHz 解码后为 `n=294082769`，按源码纯长度算术预计 **138 块**、每块约 **44.89294 秒**，拼接输出也为 **294082769 点**；这是对实现的推导，未运行全片模型。[补尾](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechEnhancement/SpeechEnhancement.swift#L100-L110) · [去延迟／按输入长裁切](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechEnhancement/SpeechEnhancement.swift#L216-L224) · [配置](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechEnhancement/Configuration.swift#L63-L83) · [拼接及最终长度裁切](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechEnhancement/SpeechEnhancement.swift#L299-L350)
- **crossfade 是 sin/cos 等功率加权，没有 overlap-add 的权重和归一化。** 两个相关波形不能仅由 `sin²+cos²=1` 推断幅度不变；若两块在接缝完全相同，中点的权重和可达 √2，即约 +3 dB。实际接缝增益、噪声底起伏与语音连续性需试听；不能宣称已经保证无响度变化。固定 main 提交在这部分也没有改成权重归一化。[0.0.23 crossfade L316–336](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechEnhancement/SpeechEnhancement.swift#L316-L336) · [main 对应方法](https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/SpeechEnhancement/SpeechEnhancement.swift#L289-L414)

官方长音频 E2E 测试覆盖 90 秒长度与数值有效性，但允许 **±0.1%** 长度误差；这不是对任意时长“采样绝对相同”的证明，也没有覆盖本片 102 分钟。[测试的长度容差](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Tests/SpeechEnhancementTests/E2EDeepFilterNet3ChunkingTests.swift#L52-L87)

### 内存与格式边界

`denoise` 仍先完整调用 `AudioFileLoader.load(...targetSampleRate:48000)`，分块阶段保留完整输入和输出数组，最后完整 `WAVWriter.write()`，**并非端到端流式**。每块模型／DSP 中间量有界，整文件 PCM 缓冲仍随时长增长；0.0.23 也仍只取第一声道。对当前 44.1 kHz stereo 片源，原格式解码 buffer 约 2.013 GiB，48 kHz mono Float32 输入约 1.096 GiB，完整增强输出约 1.096 GiB，PCM16 写出数据约 0.548 GiB；另有重采样副本、每块频谱／特征／模型输出，不能拿 100 秒样例峰值直接当全片峰值。[完整加载与调用](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCLILib/DenoiseCommand.swift#L34-L65) · [完整输出数组](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/SpeechEnhancement/SpeechEnhancement.swift#L292-L350) · [加载器与第一声道](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/AudioFileLoader.swift#L26-L50)

源码把源文件 `length` 转为 `AVAudioFrameCount`，重采样也使用该计数类型的容量；它的 UInt32 范围不是“字节数”限制。当前输入源帧数约 **270188544**，48 kHz 目标约 **294082769**，都低于 **4294967295**；约 **588165582 字节**的 PCM16 mono WAV 也低于 RIFF32 的 4 GiB 量级限制，因此这些计数范围**不构成本片的长度上限**。这不保证内存分配或系统解码一定成功，更长文件也不能无限推断。[源 buffer 容量](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/AudioFileLoader.swift#L26-L35) · [重采样容量](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/AudioFileLoader.swift#L196-L221) · [WAV 32-bit 尺寸字段](https://github.com/soniqo/speech-swift/blob/c1aa219bc2284239ff6917d675a3e1978c840260/Sources/AudioCommon/WAVWriter.swift#L11-L37)

### 本次会话的 DeepFilterNet3 实测

主任务对用户已重命名的 `outputs/20261028_original.wav`（精确 100 秒、44.1 kHz stereo）完成缓存模型测试：默认 45 秒／500 ms 参数分成 **3 块**，加载 **4800000 点／48 kHz**，输出也为 **4800000 点／48 kHz mono，精确 100 秒**，ffmpeg 全文件解码通过。总墙钟 **17.275 秒**，CLI 降噪处理阶段 **14.283 秒**（RTF 约 0.14），峰值 RSS **1693302784 字节**（约 1.577 GiB）。运行中出现与先前相同的非致命 Core ML shape 警告，随后 Ready 并成功；没有把警告本身当成此次失败。该实测支持长于模型单次上限时分块路径确实工作，未验证全长。按此粗估 6126.7 秒文件可能在 **15–20 分钟量级**，实际仍取决于解码、分块长度、设备负载及热状态，不保证时间或峰值内存。此外，对原始 M4A 的 20 秒 AAC 截段直接调用本项目 denoise_audio 已成功，总耗时 4.957 秒，输出精确 20.000000 秒、48 kHz mono；这确认该文件编码可直接输入，无需先转换整个文件，但不等同于全片验收。

**建议**：清理背景噪声并保留原节奏时，可优先试默认 `denoise` 长音频路径；先明确选声道或混音，若预解码，选择 **48 kHz mono PCM16 WAV**，不要沿用 Sidon 的 16 kHz 中间格式而无谓丢掉高频。保留 45 秒／500 ms 默认值，不盲目并发；36 GiB 下先测实际内存压力。输出长度预期与 48 kHz 解码输入一致，但仍应核对全片样本数、开头／中段／末段同步、约 45 秒块边界的响度和书写噪声与说话重叠处。该源码核查不证明它能彻底去掉所有书写声，也不把保持样本数等同于已经验证全片音质。
