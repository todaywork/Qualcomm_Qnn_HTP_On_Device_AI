# Genie CLI 与 Android 推理参数统一说明

更新日期：2026-08-15

## 1. 本次修改范围

本次只修改了下面的源码文件：

- `genie_cli/run_genie_cli.py`

脚本运行时会为每条语料生成 Android 对齐配置，并推送到设备：

```text
/data/local/tmp/genie_qwen3_quality/genie_cli/android_aligned_genie_config.json
```

该文件是运行时临时配置。bundle 根目录原来的
`/data/local/tmp/genie_qwen3_quality/genie_config.json` 不会被覆盖。

## 2. 为什么需要修改

原来的 `run_genie_cli.py` 直接使用 bundle 自带的 `genie_config.json`：

```text
--config genie_config.json
```

但 Android JNI 并没有直接加载这份 JSON，而是在
`qwen3_genie_jni.cpp` 的 `buildConfig()` 中重新生成配置。因此两边原来存在
context、Token 上限、采样器、线程数和 HTP poll 等差异。

现在 `run_genie_cli.py` 会生成与 Android JNI 默认值一致的 JSON，再把生成的
JSON 传给 `genie-t2t-run`。

## 3. 已统一的参数

| 参数 | 原 bundle/CLI | Android 默认值 | 修改后 CLI | 当前模拟量化脚本 `run_smoke20.py` |
|---|---:|---:|---:|---|
| Context size | 4096 | 512 | 512 | 512（实际传入 `make_generator`，一致） |
| `max_all_token` | 无脚本级限制 | 256 | 256 | 256（脚本动态校验，一致） |
| 单次最大输出 Token | 配置未指定 | 48 | 48 | `min(48, 256 - Genie输入Token数)`（一致） |
| Stop sequence | 未指定 | `<\|im_end\|>` | `<\|im_end\|>` | `<\|im_end\|>`、`<\|endoftext\|>` 和 tokenizer EOS（存在额外停止 Token） |
| Seed | 42 | 42 | 42 | 42（每条语料生成前重置，一致） |
| Temperature | 0 | 0 | 0 | 0（已设置；Greedy 模式下不参与采样） |
| Top-K | 40 | 40 | 40 | 40（已设置；Greedy 模式下不参与采样） |
| Top-P | 0.95 | 0.95 | 0.95 | 0.95（已设置；Greedy 模式下不参与采样） |
| Greedy | 未指定 | `true` | `true` | `true`（通过 `do_sample=false` 生效，一致） |
| Penalize last N | 未指定 | 128 | 128 | 仅记录参考值 128；HF generator 无对应控制项，未生效 |
| Repetition penalty | 未指定 | 1.0 | 1.0 | 1.0（通过 `GenerationConfig` 生效，一致） |
| Presence penalty | 未指定 | 0.0 | 0.0 | 仅记录参考值 0.0；HF generator 无对应控制项，未生效 |
| Frequency penalty | 未指定 | 0.0 | 0.0 | 仅记录参考值 0.0；HF generator 无对应控制项，未生效 |
| Engine threads | 3 | 4 | 4 | 不适用；Python/AIMET 脚本不控制 Genie Engine 线程数 |
| HTP `use-mmap` | `true` | `true` | `true` | 不适用；未运行 Genie/QNN HTP backend |
| HTP spill-fill buffer | 0 | 0 | 0 | 不适用；未运行 Genie/QNN HTP backend |
| HTP mmap budget | 0 | 0 | 0 | 不适用；未运行 Genie/QNN HTP backend |
| HTP poll | `true` | `false` | `false` | 不适用；未运行 Genie/QNN HTP backend |
| CPU mask | `0xe0` | `0xe0` | `0xe0` | 不适用；Python/AIMET 脚本未设置 CPU mask |
| KV dimension | 128 | 128 | 128 | 未显式设置；由模型结构和 generator 内部决定 |
| Async init | `false` | `false` | `false` | 不适用；Python/AIMET 模型加载不使用 Genie async init |
| Position ID dimension | 64 | 64 | 64 | 未显式设置；由模型配置和 generator 内部决定 |
| RoPE theta | 1000000 | 1000000 | 1000000 | 未显式设置；读取模型配置，不由脚本覆盖 |
| CLI log level | `error` | `verbose` | `verbose` | 不适用；不是 `genie-t2t-run` CLI |

模拟量化脚本与 Android/Genie 参数的核对结论：

- 已实际对齐：Context size、`max_all_token`、动态最大输出 Token、Seed、Temperature、Top-K、Top-P、Greedy 和 Repetition penalty。
- 存在行为差异：模拟量化脚本除 `<|im_end|>` 外，还会在 `<|endoftext|>`/tokenizer EOS 时停止。
- 仅打印但未生效：Penalize last N、Presence penalty、Frequency penalty。
- 不适用或不能由该脚本验证：Engine threads、全部 HTP 参数、CPU mask、Async init 和 CLI log level。
- 由模型/generator 内部决定而非脚本显式覆盖：KV dimension、Position ID dimension 和 RoPE theta。

模型资源路径也改成了与 Android JNI 相同的设备绝对路径：

```text
/data/local/tmp/genie_qwen3_quality/tokenizer.json
/data/local/tmp/genie_qwen3_quality/htp_backend_ext_config.json
/data/local/tmp/genie_qwen3_quality/part1_of_2.bin
/data/local/tmp/genie_qwen3_quality/part2_of_2.bin
```

DSP 搜索路径从：

```text
/vendor/lib/rfsa/adsp;$PWD/dsp
```

改为与 Android `configureRuntimePaths()` 一致的：

```text
/vendor/lib/rfsa/adsp;$PWD;$PWD/dsp;$PWD/lib
```

当前输入流程：

```
空 KV Cache + 相同 Input IDs + COMPLETE + 相同模型与配置
```

唯一的实现差别是：

- CLI 通过销毁进程和重新创建 Dialog 获得空状态。
- App 通过 `GenieDialog_reset()` 清空已有 Dialog 状态。

经验证：上述差别不影响。输入和输出完全一致。



## 4. Token 上限如何与 Android 对齐

Android 不是永远固定生成 48 个 Token，而是先计算完整 raw prompt 的 Token 数，
再计算本条语料的实际输出预算：

```text
实际输出上限 = min(48, 256 - Genie输入Token数)
```

当输入 Token 数达到或超过 256 时，Android 会报错，因为已经没有输出预算。
CLI 脚本现在采用相同逻辑。

Python `tokenizers` 在 `add_special_tokens=False` 时不会计入 Genie 使用的 BOS，
当前模型实测需要额外加 1：

```text
Genie输入Token数 = Full raw prompt tokens + 1
```

设备实测：

```text
Prompt: 关闭天窗
Full raw prompt tokens : 54
Genie prompt tokens    : 55
Profile prompt tokens  : 55
```

## 5. Prompt 已保持一致

Android `PromptBuilder.java` 和 CLI `make_raw_prompt()` 当前都使用：

```text
<|im_start|>system
You are a multilingual vehicle control assistant. Extract intent and slots from user commands in any language. Always respond with English JSON only. Do not think or explain.
<|im_end|>
<|im_start|>user
用户输入
<|im_end|>
<|im_start|>assistant
<think>

</think>

```

这部分本次没有改变内容，但换模型时必须重新核对。聊天模板、换行或 thinking
前缀不同都会改变 Token ID 和输出结果。

## 6. `run_genie_cli.py` 中对应的修改位置

### 6.1 Android 默认常量

文件开头的 `ANDROID_*` 常量：

```python
ANDROID_CONTEXT_SIZE = 512
ANDROID_MAX_ALL_TOKENS = 256
ANDROID_MAX_OUTPUT_TOKENS = 48
ANDROID_BOS_TOKEN_COUNT = 1
ANDROID_THREAD_COUNT = 4
ANDROID_SEED = 42
ANDROID_TOP_K = 40
ANDROID_TOP_P = 0.95
ANDROID_TEMPERATURE = 0.0
ANDROID_PRESENCE_PENALTY = 0.0
```

### 6.2 Android 对齐配置生成

函数：

```text
make_android_aligned_config()
```

这里包含 context、词表信息、采样器、penalty、线程数、QnnHtp 参数以及模型文件名。

### 6.3 Prompt Token 计算

函数：

```text
print_prompt_tokens()
```

这里使用 `ANDROID_BOS_TOKEN_COUNT` 把 Python Token 数换算成 Genie Token 数。

### 6.4 每条语料的动态输出预算

函数：

```text
run_case()
```

这里计算 `min(48, 256 - 输入Token数)`，生成配置文件并推送到设备。

### 6.5 Genie CLI 命令

当前实际命令等价于：

```sh
./genie_cli/bin/genie-t2t-run \
  --config /data/local/tmp/genie_qwen3_quality/genie_cli/android_aligned_genie_config.json \
  --prompt_file /data/local/tmp/genie_qwen3_quality/genie_cli/batch_prompt.txt \
  --log verbose \
  --profile /data/local/tmp/genie_qwen3_quality/genie_cli/batch_profile.json
```

## 7. 下次更换模型必须检查和修改的参数

不要只替换 `.bin` 和 `tokenizer.json`。建议按下面顺序检查。

### 7.1 先确认 Android 端的权威值

CLI 的目标是匹配 Android，因此优先检查新 Android 工程中的：

1. `MainActivity.java`
   - 默认 context；
   - `max_all_token`；
   - 最大输出 Token；
   - thread count；
   - greedy、top-k、top-p、temperature、presence penalty；
   - 默认 System Prompt。
2. JNI 配置生成文件，例如 `qwen3_genie_jni.cpp`
   - `buildConfig()` 的全部 JSON 字段；
   - stop sequence；
   - token penalty；
   - QnnHtp 参数；
   - 模型文件名和数量；
   - DSP 搜索路径。
3. Prompt 构造文件，例如 `PromptBuilder.java`
   - system/user/assistant 标签；
   - 换行；
   - thinking/no-thinking 模板。

如果 Android 改为直接读取 `genie_config.json`，则应该让 CLI 使用相同 JSON，
不要继续维护两份手工参数。

### 7.2 修改脚本顶部常量

根据新 Android 默认值更新：

- `ANDROID_CONTEXT_SIZE`
- `ANDROID_MAX_ALL_TOKENS`
- `ANDROID_MAX_OUTPUT_TOKENS`
- `ANDROID_THREAD_COUNT`
- `ANDROID_SEED`
- `ANDROID_TOP_K`
- `ANDROID_TOP_P`
- `ANDROID_TEMPERATURE`
- `ANDROID_PRESENCE_PENALTY`

`ANDROID_BOS_TOKEN_COUNT` 不应凭经验填写。先跑一条 single 推理，计算：

```text
ANDROID_BOS_TOKEN_COUNT = Profile prompt tokens - Full raw prompt tokens
```

当前 Qwen3 模型该值为 1。新 tokenizer/chat template 可能不同。

### 7.3 修改 `make_android_aligned_config()`

必须从新模型 bundle 和 Android JNI 核对：

- `context.size`
- `context.n-vocab`
- `context.bos-token`
- `context.eos-token`
- stop sequence
- sampler 全部字段
- token penalty 全部字段
- `engine.n-threads`
- backend 类型
- `use-mmap`
- `spill-fill-bufsize`
- `mmap-budget`
- `poll`
- `cpu-mask`
- `kv-dim`
- `allow-async-init`
- `pos-id-dim`
- `rope-theta`
- extensions 文件名
- context binary 文件名、数量和顺序

这些值应来自新模型导出的 `genie_config.json`、`metadata.json` 和 Android JNI，
不要根据模型名称猜测。

### 7.4 修改模型文件检查列表

当前脚本固定检查：

```text
part1_of_2.bin
part2_of_2.bin
tokenizer.json
htp_backend_ext_config.json
dsp/libQnnHtpV73Skel.so
```

如果新模型变成一个、三个或更多 context binary，需要同时修改：

- `deploy_runtime()` 中的设备文件检查列表；
- `make_android_aligned_config()` 中的 `ctx-bins`；
- 文件顺序必须与新 bundle 的 `genie_config.json` 相同。

### 7.5 修改设备目录和本地 tokenizer 路径

如果新模型部署目录变化，修改：

```python
DEFAULT_DEVICE_ROOT = "/data/local/tmp/新的模型目录"
```

如果 bundle 目录结构变化，修改：

```python
DEFAULT_TOKENIZER = LOCAL_ROOT.parent / "tokenizer.json"
```

也可以运行时使用：

```powershell
python .\run_genie_cli.py `
  --device-root /data/local/tmp/新的模型目录 `
  --tokenizer E:\path\to\new_model\tokenizer.json
```

### 7.6 修改 Prompt 模板

如果新模型不是当前 Qwen3 chat template，修改 `make_raw_prompt()`：

- special token；
- system/user/assistant 标签；
- system prompt；
- thinking/no-thinking 写法；
- 结尾换行和 assistant generation prefix。

同时修改或确认 Android 的 PromptBuilder，确保两边生成的完整 prompt 字节完全一致。

### 7.7 检查 SoC、DSP 架构和运行库

当前模型针对 SA8775P/HTP V73，脚本和文件检查中包含：

```text
libQnnHtpV73Stub.so
dsp/libQnnHtpV73Skel.so
```

更换 SoC、HTP 架构或 QAIRT/Genie 版本时必须重新检查：

- `htp_backend_ext_config.json` 中的 `soc_model` 和 `dsp_arch`；
- Stub/Skel 文件名；
- `libGenie.so` 与 QNN 动态库版本；
- Android APK 内动态库与 CLI `libs` 目录动态库是否来自同一套运行时。

可在 PowerShell 中比较 SHA-256：

```powershell
Get-FileHash .\libs\libGenie.so -Algorithm SHA256
Get-FileHash D:\path\to\android\app\src\main\jniLibs\arm64-v8a\libGenie.so -Algorithm SHA256
```

本次检查的 Genie/QNN/HTP 运行库哈希一致，因此没有替换动态库。

## 8. 换模型后的验证步骤

### 8.1 Python 语法检查

```powershell
python -m py_compile .\run_genie_cli.py
```

### 8.2 查看命令参数

```powershell
python .\run_genie_cli.py --help
```

### 8.3 单条设备推理

设备上已经部署正确运行库时：

```powershell
python .\run_genie_cli.py `
  --mode single `
  --skip-upload `
  --no-print-token-ids
```

首次部署或需要覆盖 CLI 运行库时去掉 `--skip-upload`。

通过条件：

- 配置可以加载；
- 推理退出码为 0；
- 输出非空；
- `Genie prompt tokens` 与 `Profile prompt tokens` 相同；
- 没有 context exceeded、模型文件缺失、ELF 架构错误或 DSP 加载错误。

### 8.4 检查设备上的最终配置

```powershell
adb shell cat /data/local/tmp/genie_qwen3_quality/genie_cli/android_aligned_genie_config.json
```

将这份 JSON 与 Android JNI 实际生成的 JSON 逐字段比较。

### 8.5 使用同一条语料对比 Android 与 CLI

至少比较：

- 完整 prompt Token ID；
- Prompt Token 数；
- 生成 Token 数；
- 最终文本或 JSON；
- context、采样器和 Token 上限；
- 使用的模型 binary、tokenizer、Genie/QNN 动态库哈希。

## 9. 仍然存在的非配置差异

参数统一后，两边的进程生命周期仍不同：

- Android 批跑复用 Genie Dialog，后续查询使用 REWIND；
- 当前 CLI 脚本每条语料启动一个新的 `genie-t2t-run` 进程。

这不会改变本文列出的配置值，但可能影响初始化耗时、缓存状态和性能指标。
如果后续要求严格比较性能或完整执行状态，需要进一步统一 Dialog 生命周期，不能只比较 JSON 参数。
