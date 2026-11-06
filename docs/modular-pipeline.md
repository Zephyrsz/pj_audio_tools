# 模块化语音工具与切句 pipeline

实现日期：2026-11-05。Python >=3.10，无第三方 Python 运行时依赖。FFmpeg/ffprobe负责解码和导出，模型通过所选外部工具或HTTP服务运行。

## 模块边界

| 模块 | 责任 |
|---|---|
| models.py | ToolConfig、时间区间、转写、对齐单位、句子及五类工具协议 |
| registry.py | 按“功能 + provider”创建适配器 |
| providers/soniqo.py | speech CLI适配 |
| providers/compatible.py | HTTP ASR、Chat Completions和TTS |
| providers/openrouter_asr.py | OpenRouter JSON/Base64 ASR与本地密钥文件读取 |
| batch_asr.py | WAV目录时长过滤、批量ASR、检查点恢复和音文清单导出 |
| providers/command.py | argv/stdin/stdout JSON协议，接入其他工具 |
| providers/energy.py | 无模型能量基线，不具有人声分类能力 |
| tools.py | 独立转写、对齐、VAD、LLM、TTS接口 |
| segmentation.py | 原文覆盖、规则分句、受限LLM断句和句子padding |
| pipeline.py | 切句入口：输入/范围校验、创建后端、打开任务目录、启动任务流 |
| sentence_flow.py | 任务流：prepare→vad→asr→align→segment→export 阶段编排、缓存读写、时间偏移 |
| timeline.py | 纯时间规则：工作窗口规划、跨窗单元合并、低能量切点微调 |
| workspace.py | 任务目录所有权检查、锁、子目录、JSONL阶段日志 |
| cache_keys.py | 后端缓存键（配置、适配器、可执行文件、参考音频） |
| cache.py | 内容寻址缓存与完整性检查 |
| audio.py / export.py | PCM、声学边界、原录音切片、字幕和数据集清单 |
| cli.py | 工具/pipeline命令；原extract/denoise/restore接口保留 |

依赖方向：CLI → tools/pipeline → 协议与Registry → provider；pipeline → sentence_flow → timeline/segmentation/workspace/cache_keys → audio/storage/cache，下层不引用上层。pipeline不按模型名选择算法；可用Registry注入其他实现。provider选择工具，model选择模型，options.engine选择Soniqo内部引擎。

## 当前后端

| 功能 | providers |
|---|---|
| ASR | soniqo、openai-compatible、openrouter、command |
| 强制对齐 | soniqo、command |
| VAD | soniqo、energy、command |
| LLM | openai-compatible、command；pipeline中null表示规则 |
| TTS | soniqo、openai-compatible、command；pipeline中null表示关闭 |

具体模型ID由工具/服务解释，不维护容易过期的全局模型列表。未知后端或不支持的模型会报错，不静默换成别的模型。

Soniqo的qwen3/parakeet批量ASR复用模型并读取每文件TXT，避开混合日志JSONL。配置context或其他引擎时逐文件转写。对齐明确传language和aligner-model，逐个短工作块执行。align CLI仍逐块加载模型，超长任务有加载开销；可以通过command或自定义provider接入常驻模型进程。

Soniqo TTS模型flag按engine映射：qwen3用--model，cosyvoice用--model-id，voxcpm2用--voxcpm2-variant，indextts2/f5/higgs/indic-mio用各自model-id，magpie用variant。并非每个引擎都接受任意HF模型ID，具体能力以本机speech为准。

## pipeline

1. 每任务一个原音频及可选[start,end)范围，本地记录路径、SHA-256、时长、声道和配置。
2. 准备16kHz mono PCM16分析轨；原录音用于最终导出。channel可选mix、0、1，不把Sidon重建轨当作原时间轴。
3. 默认24秒正文、两侧0.6秒上下文，附近2秒搜索停顿；短尾块尽量合并到前块。window_seconds是目标长度，非硬上限。skip_silence=true可能漏掉轻声，可改false；windows.json记录跳过范围。
4. ASR每组最多32块，组间缓存。不同模型结果独立保存。
5. 对齐校验全文覆盖，以单位时间中点确定正文归属。上下文重叠不重复导出；相同单位的相交结果可去重。跨窗口不同文字最多81ms的时间冲突以中点接合并标记alignment_overlap_adjusted，更大冲突报错。跨正文边界、能填入已覆盖音频之外空隙且不冲突的上下文词可救回，标记window_boundary_rescued。跨窗口首末单位标记window_boundary，句子可跨窗口；这些规则不能保证修复模型漏听或错字，须试听复核。
6. 规则模式按句末标点分句，不单凭停顿判断完句。英文缩写等复杂标点仍需LLM或人工复核。LLM只允许返回end_unit_ids递增数组，覆盖全文；不接受改写、翻译和新时间戳。每批约160单位，尽量在句末切分上下文，极长无标点文本会要求校对。
7. 零时长词保留并标记zero_duration_unit，不插值造词级时间。独立句子的全部词都为零时长时，保留文字并入下一个有时长的句子；末尾则并入前句，标记zero_duration_sentence_merged。若整份对齐都无正时长，拒绝导出。部分跨越文件尾部、最多81ms的越界裁回边界并标记alignment_boundary_clipped；更大的部分越界、倒序、同块重叠和漏词报错。pipeline允许极少量完全落在窗口音频尾部之外的词（最多3个对齐单位，规范化后合计最多12字符），将原始文字和源时间保存到alignment.json的unassigned_tail及dataset/alignment_issues.json，不给这些字伪造音频；涉及最后窗口时，末句标记unassigned_tail_text。超过上限报错。独立align工具不会启用这个尾文例外。
8. padding默认前100ms、后200ms；在已对齐讲话之外参考邻近低能量点微调。超过30秒只标记，不强切半句。
9. 原录音重新编码为WAV，保留源采样率/声道。全局SRT使用原录音时间，单句SRT减cut_start。处理子范围的首末句标记range_start/range_end，提示范围可能截在句中。
10. 配置TTS时额外导出tts/合成音频和覆盖其全时长的一条字幕，绝不替换wav/原录音切片；按文本、语言、模型缓存。合成字幕为展示范围，不宣称词级对齐。

语义质量依赖原始转写、标点、对齐和LLM。review.json列出需要试听复核的句子，不保证任意口语都能自动形成完整句。

上下文尾部越界补充：如果词的开始位置已经处于正文core_end之后，且结束位置超过工作块音频，pipeline保留其原始时间并标记context_tail_overrun，不让该上下文词阻塞正文导出。它不会归入本窗口正文，下一窗口按自己的ASR与对齐结果处理；不保证下一窗口一定识别到相同文字。此类词同样受最多3个单位/12个规范化字符的审计上限约束，记录到alignment_issues.json，reason为context_tail_exceeds_window_audio。现有unassigned_tail_count统计该文件内全部记录，包括这类上下文记录。正文内的严重越界仍报错，独立align也保持严格检查。

每个窗口原始对齐结果保存在logs/align-window-NNNNNN.json。对齐验证失败时，CLI显示窗口编号、具体词、起止秒数、前词结束和音频时长及诊断路径；恢复任务使用原命令加--resume，复用已经完成的ASR和有效对齐缓存。

## 配置

configs/sentences.soniqo.json可用于本机日语样本。各功能工具配置统一为：

```json
{"provider":"soniqo","model":"0.6B","options":{"engine":"qwen3","timeout":1800}}
```

llm/tts为null表示关闭。启用本地LLM时，将下面对象放到完整pipeline JSON的llm字段，并将model改为服务实际加载的ID：

```json
{
  "provider": "openai-compatible",
  "model": "your-loaded-model-id",
  "options": {
    "base_url": "http://127.0.0.1:1234/v1",
    "api_key_env": null,
    "json_mode": true,
    "timeout": 300
  }
}
```

兼容服务须实现对应的/audio/transcriptions、/chat/completions或/audio/speech，不要求同时具备全部功能。云服务指定base_url和api_key_env名称，密钥值只读环境变量；禁止配置明文api_key/password/token或在URL嵌入凭据。HTTP不跟随重定向，不自动重试付费请求。

OpenRouter独立ASR配置见configs/asr.openrouter.json，默认沿用tts_test后台的google/gemini-3.5-transcribe模型。其JSON/Base64适配器优先读取OPENROUTER_API_KEY，其次读取项目configs/or.cfg；options.api_key_file可指定其他密钥文件。目录批量任务通过asr-batch或python3 -m voice_tools.batch_asr运行，默认保留文件时长严格大于2秒的WAV，输出wav/、transcript_utf8.txt和batch_asr.json；接口与恢复规则见README中的OpenRouter章节。

ASR把Japanese等名称适配为ja，Soniqo反向适配。TTS兼容接口使用model、voice、input和WAV输出，不发送language字段。LLM可配置temperature、max_completion_tokens或max_tokens；服务不支持response_format时可设json_mode=false，但pipeline仍检查返回JSON。

CLI覆盖配置对应字段，其余参数保留。--asr-model 1.7B只改ASR模型。未知pipeline字段严格报错。configs/llm.local.json是单工具配置示例，其model须先修改；不表示已经为用户安装或启动LLM服务。

## command扩展协议

```json
{"provider":"command","model":"your-model","options":{"argv":["/absolute/path/to/worker"],"timeout":300}}
```

不经过shell，不在命令字符串中替换用户文字。stdin为一个JSON对象，通用字段schema_version=1、kind、model；stdout为一个JSON对象，日志写stderr。

| kind | 附加请求字段 | 响应 |
|---|---|---|
| asr | audio_path, language | {"text":"原文","language":"Japanese"} |
| aligner | audio_path, text, language | {"words":[{"text":"词","start":0.1,"end":0.4}]} |
| vad | audio_path | {"spans":[{"start":0.1,"end":0.4}]} |
| llm | messages, json_mode | {"text":"模型输出"} |
| tts | text, output_path, language | 写指定output_path并返回{"ok":true} |

时间为输入audio_path的局部秒数，pipeline负责offset。由用户配置的command在本机执行，需保证外部程序可用。同进程扩展可调用Registry.register(kind,name,factory)，factory接收ToolConfig并返回相应协议对象；ASR可实现transcribe_many复用模型。

## 产物和恢复

```text
task/
  task.json
  windows.json
  transcript.raw.json
  alignment.json
  sentences.json
  logs/process.jsonl
  cache/
  work/
  dataset/
    wav/000001.wav
    txt/000001.txt
    srt/000001.srt
    transcript_utf8.txt
    subtitles.srt
    manifest.json
    review.json
    alignment_issues.json      # 无法分配音频的尾文及原始时间
    tts/                       # 仅配置TTS时
```

transcript_utf8.txt每行“文件stem:单行字幕”，音文一一对应，兼容现有datasets_manager。manifest记录真实源时间、切片时间、模型配置和散列。数据库登记走已有管理器入口，本模块不自动上传/登记。

首次要求空任务目录；--resume要求同一原文件内容、start/end、声道。更改ASR重算对应转写及依赖对齐，更改LLM只重分句，更改TTS只重合成。缓存受输入、配置、外部程序mtime约束；TTS的voice_sample额外记录文件内容SHA-256，工具或参考音频变化同时使合成缓存和导出缓存失效。HTTP模型别名若服务端换权重，应改model标识或新建任务。

导出先完整staging后发布，失败保留旧数据集和缓存。相同内容未改的导出直接复用；人工修改或缺文件阻止覆盖，请选择新任务。进程被强制终止如遗留.lock，确认进程停止后再手动移走，不自动抢锁。WAV通常比AAC占用更多磁盘空间，原录音散列在发布前再核对。

自动化测试使用合成音频、假的模型和本地HTTP服务，不调用收费接口或登记真实数据库。真实Soniqo短样本验证见[sentence-pipeline-validation.md](sentence-pipeline-validation.md)，不将程序链路通过解释为已经验证全片语义质量。
