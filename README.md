# 语音处理工具集

提供命令行和 Python 接口：

- `extract`：按开始、结束时间截取音频。
- `denoise`：使用 Soniqo DeepFilterNet3 抑制语音背景噪声，优先用于书写、纸张摩擦等场景。
- `restore`：使用 Soniqo Sidon 独立生成降噪、去混响、带宽恢复版本，供试听比较。
- `asr` / `align` / `vad` / `llm` / `tts`：独立调用工具，按 provider、model、options 选择实现。
- `asr-batch`：扫描 WAV 目录，按时长过滤，批量识别并导出音频和字幕清单。
- `sentences`（别名 `pipeline`）：编排切句和字幕导出，支持缓存恢复和可选 TTS。

## 环境

- Python 3.10 或更新版本，无第三方 Python 运行时依赖。
- FFmpeg 和 ffprobe 在 PATH 中。本机已安装；其他 macOS 环境可用 `brew install ffmpeg`。
- `denoise` / `restore` 额外需要 Soniqo 的 `speech` CLI（Apple Silicon macOS），可用 `brew install speech` 安装。首次运行会从 Hugging Face 下载对应模型；模型缓存后可本地处理，下载/权限/模型加载失败时命令返回错误。本机验收版本为 `speech 0.0.23`。
- FFmpeg 是截取模块的依赖；增强模块直接调用 `speech`，不会增加 Python 第三方运行时依赖。

## 命令行

在项目目录运行：

```bash
cd /Users/rgwei/pj/pj_audio/voice_tools
python3 -m voice_tools extract 'raw/20261028井上和.m4a' \
  --start 0 --end 100
```

示例输出：`outputs/20261028井上和_0-100.wav`。该文件已生成，再次运行需加 `--overwrite`，或用 `-o` 指定其他路径。

时间也支持 `MM:SS`、`HH:MM:SS` 和小数秒。例如截取第 1 分 30.5 秒到第 2 分 10 秒：

```bash
python3 -m voice_tools extract 'raw/20261028井上和.m4a' \
  --start 01:30.500 --end 02:10 \
  -o outputs/clip.wav
```

指定其他输出格式：

```bash
python3 -m voice_tools extract 'raw/20261028井上和.m4a' \
  --start 0 --end 100 -o outputs/first_100_seconds.m4a
```

查看帮助：`python3 -m voice_tools extract --help`。也可在虚拟环境中运行 `python3 -m pip install -e .`，随后使用 `voice-tools extract ...`。

## Python 接口

在项目目录运行 Python，或先安装此项目：

```python
from voice_tools import AudioError, extract_clip

try:
    output = extract_clip(
        input_path="raw/20261028井上和.m4a",
        start="00:00",
        end="01:40",
        output_path="outputs/python_example.wav",
    )
    print(output)  # pathlib.Path，输出绝对路径
except AudioError as error:
    print(f"截取失败：{error}")
```

`output_path` 可省略，默认写入当前工作目录下的 `outputs`。需要覆盖已有输出时显式传入 `overwrite=True`。原文件及其软链接、硬链接均不允许作为输出。

## 时间与输出规则

- `end` 是原音频上的结束位置，不是片段长度；区间为 `[start, end)`。
- 必须满足 `0 <= start < end <= 音频时长`。越界会报错，不会静默缩短。
- 选择第一个音频轨道；保持原采样率和声道数，不做降噪或音量调整。
- WAV：16 位 PCM，默认格式；M4A：AAC 192 kbps；MP3：可变码率质量 2；FLAC：无损压缩。
- 通过解码后重新编码进行截取。WAV/FLAC 的边界受采样间隔限制；有损格式可能有编码延迟、填充和容器时长误差，并可能再次损失音质。WAV 导出不会恢复原 AAC 已丢失的信息。
- 写入先使用同目录临时文件，成功后发布；失败时清理临时文件，保护已有输出。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

集成测试需要 FFmpeg，覆盖真实 PCM 样本位置、片段长度、采样率与声道、AAC 输入、多种输出格式、非法时间、路径保护以及命令行行为。

用户音频样例已验证：前 100 秒导出为 44,100 Hz、双声道、16 位 PCM WAV，时长 100.000000 秒。

## 语音降噪与修复

针对人声加书写、翻书、纸张摩擦声，先用 DeepFilterNet3。它不能保证完全删除与讲话重叠的瞬态噪声；需要保留清楚的辅音、呼吸和音色，不能仅凭背景更安静判断结果更好。

```bash
python3 -m voice_tools denoise 'outputs/20261028井上和_0-100.wav'
```

默认保存为 `outputs/20261028井上和_0-100_denoised.wav`。样例已经生成这个文件，再运行请加 `--overwrite`，或用 `-o` 指定其他 WAV 路径。

如降噪后仍有明显混响或严重纸张声，从**原录音**独立生成 Sidon 版本：

```bash
python3 -m voice_tools restore 'outputs/20261028井上和_0-100.wav'
```

默认保存为 `outputs/20261028井上和_0-100_restored.wav`。两个命令互不串联；Sidon 会通过声码器重建波形，可能改变语音细节，请和原始录音及 denoise 版本比较。

### 参数与输出规则

- 两个命令均支持 `-o / --output`、`--overwrite`、`--model`。Python 接口还可用 `executable` 指定自定义 `speech` 可执行文件路径。
- `denoise` 可用 `--chunk-seconds`、`--overlap-ms`、`--no-chunk`。默认不传这些参数，使用本机 `speech` 的默认设置；验收版本为 45 秒分块、500 ms 交叉淡化。禁用分块后，超过模型 60 秒单次上限的录音会失败。
- `restore` 可用 `--variant fp16` 或 `--variant int8`。省略时使用本机 `speech` 的默认精度。
- 建议输入 WAV；其他输入格式取决于安装的 `speech` 版本能否读取。输出只支持 PCM WAV。
- 本机两个模型的默认目标输出为 48 kHz 单声道；不会保留源双声道布局，升采样也不等于恢复原录音已丢失的信息。处理结果的样本数、时长可能随模型版本和分块实现而变化，不适合作为需要采样精确对齐的原文件替身。
- 原文件、其软链接及硬链接均不能作为输出；已有输出须显式允许覆盖。先写同目录临时文件并完整检查 WAV 数据，成功后发布；失败或中断时清理临时文件。
- 捕获到的模型错误会转换为 `AudioError`；CLI 显示错误并返回非零状态。模型运行和下载可能需要等待。

高级参数示例：

```bash
python3 -m voice_tools denoise input.wav -o outputs/clean.wav \
  --chunk-seconds 30 --overlap-ms 250
python3 -m voice_tools restore input.wav -o outputs/restored_int8.wav --variant int8
python3 -m voice_tools denoise --help
python3 -m voice_tools restore --help
```

### Python 接口

```python
from voice_tools import AudioError, denoise_audio, restore_audio

try:
    clean = denoise_audio(
        "outputs/20261028井上和_0-100.wav",
        "outputs/clean.wav",
    )
    restored = restore_audio(
        "outputs/20261028井上和_0-100.wav",  # 独立处理原录音
        "outputs/restored.wav",
        variant="fp16",
    )
    print(clean, restored)  # 均为 pathlib.Path，绝对路径
except AudioError as error:
    print(f"语音处理失败：{error}")
```

完整签名：

```python
denoise_audio(input_path, output_path=None, *, overwrite=False,
              executable="speech", model=None, chunk_seconds=None,
              overlap_ms=None, no_chunk=False) -> Path
restore_audio(input_path, output_path=None, *, overwrite=False,
              executable="speech", model=None, variant=None) -> Path
```

### 选型与验收边界

第一版不包含 VAD 间隙衰减，也不调用 HTDemucs / Open-Unmix。VAD 适合后续降低讲话间隙的噪声；音乐源分离模型针对 vocals/drums/bass/other，并不是纸张噪声的默认解决方案。

自动化测试使用假的 `speech` 程序验证子进程调用与输出保护，不下载模型，不宣称评估听感。样例模型验收单独运行，检查源文件散列、输出时长、声道、采样率及完整解码；它证明程序链路可用，不能代替试听评估残留噪声与人声失真。

2026-11-04 的样例验收：DeepFilterNet3 为 100.000000 秒；Sidon fp16 为 99.794583 秒，比原音频短约 0.205 秒。两个输出均为 48 kHz 单声道 PCM WAV，完整解码通过；原录音散列未变。具体命令与结果见 [验收记录](docs/speech-enhancement-validation.md)。

官方参考：[Soniqo](https://soniqo.audio/)、[speech-swift](https://github.com/soniqo/speech-swift)、[DeepFilterNet3](https://soniqo.audio/guides/denoise)、[Sidon](https://soniqo.audio/guides/restore)、[音乐源分离](https://soniqo.audio/guides/separate)。


## 模块化工具与长音频切句

新增功能仍无需第三方Python运行时依赖。完整架构、配置和扩展协议见 [模块文档](docs/modular-pipeline.md)。Soniqo首次使用新模型可能下载权重；HTTP功能需要指定实际可用的本地或远程服务。

### 先验证一段

```bash
python3 -m voice_tools sentences 'raw/20261028井上和.m4a' \
  --config configs/sentences.soniqo.json --end 100 \
  -o outputs/my_sentence_trial
```

省略`--end 100`即处理全片，请使用新任务目录。结果在任务的`dataset/`子目录：`wav/`为原录音切片，`transcript_utf8.txt`是datasets_manager兼容清单，另有逐句TXT/SRT、全局SRT、manifest和review.json。复核标记不表示已经确认模型转写正确，应试听校对。

全片运行命令（已存在的同一全片任务恢复时加`--resume`）：

```bash
python3 -m voice_tools sentences 'raw/20261028井上和.m4a' \
  --config configs/sentences.soniqo.json \
  -o outputs/inoue_sentence_dataset
```

2026-11-05 已实际验证前100秒：4个工作窗口导出25段，10段带复核标记，另有1处窗口外尾文保存在`alignment_issues.json`。WAV完整解码、音文对应、字幕范围、源文件散列及缓存恢复检查通过。没有运行约102分钟全片，也没有完成逐句听校。详见[切句验收记录](docs/sentence-pipeline-validation.md)。

同一输入和范围恢复、将ASR改为1.7B：

```bash
python3 -m voice_tools sentences 'raw/20261028井上和.m4a' \
  --config configs/sentences.soniqo.json --end 100 \
  -o outputs/my_sentence_trial --resume --asr-model 1.7B
```

恢复须传相同start/end和声道。更换LLM只重分句，更换TTS只重合成；失败保留缓存和已发布数据集。人工修改过的导出文件不会被覆盖。日志在`logs/process.jsonl`。当前只生成本地数据集，不自动登记到管理器。

### 独立工具

```bash
python3 -m voice_tools providers
python3 -m voice_tools asr 'outputs/20261028_original.wav' \
  --provider soniqo --engine qwen3 --model 0.6B --language Japanese \
  -o outputs/asr_result.json
python3 -m voice_tools vad 'outputs/20261028_original.wav' \
  --provider soniqo -o outputs/vad_result.json
python3 -m voice_tools align 'outputs/20261028_original.wav' \
  --text-file outputs/correct_transcript.txt --language Japanese \
  -o outputs/alignment_result.json
python3 -m voice_tools tts --text 'こんにちは。' --language Japanese \
  --config configs/tts.soniqo.json -o outputs/tts_example.wav
```

align文字文件须由实际转写/校对生成，上述字幕路径不是预置文件。已有工具输出需显式`--overwrite`。ASR只返回转写，不伪装成词时间轴。TTS独立生成WAV；pipeline配置tts字段后才额外生成合成版本，原录音片段仍在wav/。

ASR可选soniqo、openai-compatible、openrouter、command；TTS可选soniqo、openai-compatible、command；LLM可选openai-compatible、command；对齐可选soniqo、command；VAD另有无模型energy基线。不同功能独立选工具和模型，兼容服务可以运行在本机。

`configs/llm.local.json`是单工具示例，先将model改为服务实际加载的ID：

```bash
python3 -m voice_tools llm --config configs/llm.local.json \
  --text '请解释语音活动检测和语义分句的区别。'
```

要在pipeline启用LLM，将该工具对象放入完整配置的llm字段；`llm: null`表示规则分句。常规HTTP后端通过api_key_env引用密钥环境变量，OpenRouter还支持下文的本地密钥文件；JSON中均不保存密钥实际值。

### Python接口

```python
from voice_tools import PipelineConfig, ToolConfig, split_sentences, transcribe_audio

config = PipelineConfig.load('configs/sentences.soniqo.json')
dataset = split_sentences(
    'raw/20261028井上和.m4a', 'outputs/python_sentence_trial',
    config=config, end=100,
)
transcript = transcribe_audio(
    'outputs/20261028_original.wav', language='Japanese',
    config=ToolConfig('soniqo', '1.7B', {'engine': 'qwen3'}),
)
print(transcript.text)
```

自定义工具通过Registry.register注册，pipeline不需要修改。新模型一般只改对应ToolConfig.model。原文覆盖检查、时间偏移、缓存和导出保护由公共模块负责。

## OpenRouter ASR 与目录批处理

基础模块为 `voice_tools/providers/openrouter_asr.py`，从 `tts_test/tts_studio_refactored/backend/infrastructure/openrouter_asr.py` 提取适配，复用当前工具集的 `Registry`、`ToolConfig` 和 `Transcript` 接口，不依赖原来的 Web 服务。使用 `/api/v1/audio/transcriptions` 的 JSON/Base64 协议，默认模型沿用 `google/gemini-3.5-transcribe`，可通过 `--model` 替换为账户可用的 STT 模型。调用会将音频上传至 OpenRouter。

密钥优先读取 `OPENROUTER_API_KEY` 环境变量，其次读取本项目的 `configs/or.cfg`（默认路径按模块位置确定，不依赖当前工作目录）。文件支持 `OPENROUTER_API_KEY=...`、`export OPENROUTER_API_KEY="..."` 或单行原始密钥；程序只解析文本，不执行配置。`--api-key-file` 可以指定其他路径，`--api-key-env` 可指定优先读取的环境变量。JSON 配置只保存密钥路径或变量名称；`configs/or.cfg` 已加入忽略规则。

单文件调用：

```bash
cd /Users/rgwei/pj/pj_audio/voice_tools
python3 -m voice_tools asr \
  outputs/inoue_sentence_dataset/dataset/wav/000001.wav \
  --provider openrouter --language ja

# 也可使用配置文件；已有输出需显式 --overwrite
python3 -m voice_tools asr \
  outputs/inoue_sentence_dataset/dataset/wav/000001.wav \
  --config configs/asr.openrouter.json -o outputs/openrouter_single.json
```

### 批量识别和时长过滤

独立批处理脚本为 `python3 -m voice_tools.batch_asr`，主 CLI 中的 `python3 -m voice_tools asr-batch` 提供同一功能。安装项目后也可用 `voice-tools-asr-batch`。输入可以是含 `wav/` 的数据集根目录，也可以直接是 WAV 目录；程序递归扫描，支持 `.wav` / `.WAV`，不读取或复用输入目录的旧字幕。

```bash
# 先预览：不会读取密钥、调用模型或创建输出
python3 -m voice_tools.batch_asr \
  outputs/inoue_sentence_dataset/dataset \
  -o outputs/my_openrouter_dataset --dry-run

# 实际处理；请使用一个尚不存在的新输出目录
python3 -m voice_tools.batch_asr \
  outputs/inoue_sentence_dataset/dataset \
  -o outputs/my_openrouter_dataset

# 同一任务中断后恢复（已成功的文件不重复调用）
python3 -m voice_tools asr-batch \
  outputs/inoue_sentence_dataset/dataset \
  -o outputs/my_openrouter_dataset --resume
```

- 默认只保留**整个 WAV 文件时长严格大于 2 秒**的音频，2 秒整也会跳过；这是文件时长过滤，不是 VAD 检测的有效讲话时长。
- `--min-duration 3` 可调整下限，`--max-duration 20` 可加上限；区间为 `(min_duration, max_duration]`，省略上限即不限。
- `--language ja` 提供语言提示，默认由模型自动识别；不做识别后的语言筛选。`--timeout 300` 调整单次 HTTP 超时；默认 180 秒。
- 输出的 `wav/` 保存原 WAV 的字节副本，不切分、不重采样；识别上传副本通过公共工具转换为 16 kHz 单声道 PCM16。基础适配器沿用原后台的本地策略：上传 WAV 不超过 15 MiB；超过时会报错，不会自动切段。
- `transcript_utf8.txt` 为 UTF-8 无 BOM，每行 `不含扩展名的文件名:字幕`。字幕换行和连续空白折叠为单个空格，避免一条字幕占用多行。保留文件编号，过滤后允许编号不连续。ASR 不提供词级时间戳，本脚本不伪造 SRT 时间轴。
- `batch_asr.json` 记录输入散列、时长、选中状态、工具配置及逐文件识别结果。默认顺序处理，遇错停止；成功结果立即保存，完整清单只在全部成功后发布。失败时目录可能包含部分 WAV 和检查点，可恢复继续。
- 恢复要求输入内容、模型、语言和过滤条件保持一致，拒绝覆盖人工修改的 WAV 或字幕清单。新任务拒绝已有输出目录，也拒绝输入和输出相互嵌套、重复字幕编号及越出输入目录的音频软链接。为兼容数据集清单，文件 stem 不能有首尾空白、冒号、反斜杠或任何换行分隔符。任务锁防止同目录并发；强制杀死进程后，确认无任务运行才可手动删除 `.batch_asr.lock`。
- 模型请求已经完成但本地检查点尚未写入时若进程崩溃，恢复仍可能重发该条请求；检查点不能保证远程计费的恰好一次语义。

输出结构：

```text
my_openrouter_dataset/
├── wav/
│   ├── 000001.wav
│   └── ...
├── transcript_utf8.txt
└── batch_asr.json
```

Python 调用：

```python
from voice_tools import ToolConfig, transcribe_audio
from voice_tools.batch_asr import transcribe_directory

result = transcribe_audio("input.wav", config=ToolConfig("openrouter"), language="ja")
print(result.text)

output = transcribe_directory(
    "outputs/inoue_sentence_dataset/dataset",
    "outputs/my_python_openrouter_dataset",
    min_duration=2,
    # max_duration=20,
    # language="ja",
)
print(output)
```

2026-11-05 已按用户更新后的目录 `outputs/inoue_sentence_dataset/dataset` 实际调用模型：27 个 WAV 中选中 14 个、过滤 13 个，输出到 `outputs/inoue_sentence_dataset_openrouter_asr/`。14 段总计 83.346 秒，字幕清单与音频一一对应、复制内容散列一致、WAV 完整性和全缓存恢复检查通过。结果是自动转写，尚未逐句听校；详见 [验收记录](docs/openrouter-asr-validation.md)。

### 使用在线 ASR 从长音频重新切句

`batch_asr` 的 OpenRouter 后端也可直接供 `sentences` pipeline 使用。完整配置为 `configs/sentences.openrouter.json`：只有 ASR 换为 `google/gemini-3.5-transcribe`，VAD、对齐和规则分句沿用本地配置。

```bash
python3 -m voice_tools sentences 'outputs/20261028井上和_0-100_denoised.wav' \
  --config configs/sentences.openrouter.json \
  -o outputs/inoue_sentence_dataset_openrouter_pipeline
```

此示例任务已完成，再运行时加 `--resume`，或指定另一个新任务目录。该流程从完整输入重新生成工作窗口和句子，输出 WAV/TXT/SRT、manifest 和复核文件；不使用目录批处理的时长过滤条件。当前仍使用本地强制对齐获得时间戳，LLM/TTS关闭。真实测试导出30段，14段带复核标记；首词零时长、跨窗重复词等仍须听校，详见[在线切句验收记录](docs/openrouter-sentence-pipeline-validation.md)。
