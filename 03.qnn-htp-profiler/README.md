# QNN HTP Profiling Platform

面向 Qualcomm Genie/QNN HTP 模型的配置驱动性能验证工具，支持资源部署、Context 元数据导出、合成输入生成、三级 Profiling 和 Markdown 报告生成。

文档版本：2026-08-21  
对应入口脚本：`01_resource_push.py`、`02_perf_validation.py`、`run_profile.py`

## 推荐工作流

当前推荐将流程拆成两个阶段：

1. `01_resource_push.py`：一次性部署运行时、模型和 Profiling 资源，并生成 Level 2/3 输入。
2. `02_perf_validation.py`：重复执行三级性能验证，不重新推送大体积资源。

这样可以避免每次性能测试都重复推送模型和合成输入。

### Windows 交互方式

在工具根目录执行：

```powershell
cd E:\QualComm\qnn-htp-profiler

.\01_resource_push.cmd
.\02_perf_validation.bat
```

两个包装脚本会扫描 `models/*/profile-config.json`：

- 只有一个模型时自动选择；
- 有多个模型时显示编号并等待选择；
- 最终分别调用对应的 Python 入口。

### Python 命令行方式

当前目录包含多个模型配置，因此建议始终显式传入 `--config`。

Qwen2.5 示例：

```powershell
python .\01_resource_push.py `
  --config .\models\qwen2.5-0.5b-instruct-cl4096\profile-config.json `
  --verbose

python .\02_perf_validation.py `
  --config .\models\qwen2.5-0.5b-instruct-cl4096\profile-config.json `
  --verbose
```

Qwen3 示例：

```powershell
python .\01_resource_push.py `
  --config .\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\profile-config.json `
  --verbose

python .\02_perf_validation.py `
  --config .\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\profile-config.json `
  --verbose
```

如果省略 `--config`，Python 入口会尝试自动发现配置；当 `models/` 下存在多个配置时会报错，要求显式指定。

## 阶段一：资源推送

`01_resource_push.py` 严格按以下顺序执行：

1. `deploy`
   - 删除并重建配置中的设备目录 `device_root`；
   - 推送模型目录到 `device_root/model/`；
   - 推送 `genie-t2t-run`、Genie/QNN Android 库和 HTP DSP 库；
   - 推送 QNN 工具链到 `device_qnn_root`；
   - 推送与 QNN 工具链版本匹配的 Profiling Reader；
   - 当 `app_compat=true` 时，额外创建兼容 App JNI 固定路径的平铺文件布局。
2. `export-context`
   - 使用设备端 `qnn-context-binary-utility` 导出每个 Context Binary 的 Graph 元数据；
   - 拉取到模型输出目录的 `context_info/context_info/`。
3. `generate-inputs`
   - 根据 Graph 元数据生成 QNN native raw 合成输入；
   - 保存到本地 `qnn_inputs/`；
   - 推送到 `device_root/qnn_inputs/`。

模型和合成输入体积通常较大，只应在以下情况重新运行阶段一：

- 首次部署；
- 更换模型、Context Binary 或 Context Length；
- 更换 QAIRT/QNN SDK；
- 更换 HTP 架构或设备；
- 设备目录被清理；
- 修改了生成输入所依赖的 Graph 元数据。

> 注意：阶段一会执行 `rm -rf device_root`。请确保配置中的 `device_root` 是独立且正确的 `/data/local/tmp/...` 目录。

## 阶段二：性能验证

自 2026-09-11 起，02 入口自动生成中文 `reports/<时间戳>-three-level-performance-analysis.md`，结构沿用 `20260820-143407-three-level-performance-analysis.md`：结论摘要、模型与图结构、Level 1、Level 2、Level 3、三级数据互证、优化建议、正式测速建议及数据附录。数值从当前采集文件计算，不复用历史结论；缺失指标显示“不可用”。图结构来自 context_info 元数据，修改时间在附录列出。

`02_perf_validation.py` 不执行部署，直接使用阶段一留在设备上的运行时、模型、QNN 工具、Reader 和合成输入。

执行顺序由配置中的 `levels` 控制，默认是：

1. Level 1：Genie 端到端 Profiling；
2. Level 2：QNN Detailed Profiling；
3. Level 3：QNN Optrace Profiling；
4. Analyze：全部启用级别成功后生成报告。

每一级会独立捕获异常并记录失败项。如果任一级失败，脚本返回非零状态并跳过报告生成；其他尚未执行的级别仍会继续尝试。

### 三级 Profiling

| 级别 | 执行工具 | 当前参数 | 主要用途 | 开销 |
|---|---|---|---|---|
| Level 1 | `genie-t2t-run` | `--profile genie-profile.json` | TTFT、Prefill/Decode 吞吐、初始化和端到端耗时 | 低 |
| Level 2 | `qnn-net-run` | `--profiling_level detailed` | Graph/算子 cycles、热点分析 | 中 |
| Level 3 | `qnn-net-run` | `--profiling_level detailed --profiling_option optrace` | Optrace、RPC/同步和执行时间线分析 | 高 |

Level 2/3 默认遍历：

- `prefill/part1`
- `prefill/part2`
- `decode/part1`
- `decode/part2`

对于包含多个 Context Length Graph 的 Context Binary，脚本会读取 `context_info`，按配置中的 `context_length` 构造相应的 `input_list` 选择器。

## Profiling Reader `.so`

`profile_lib/` 当前包含：

```text
libQnnHtpProfilingReader.so
libQnnHtpOptraceProfilingReader.so
```

用途分别为：

- `libQnnHtpProfilingReader.so`：Level 2 Detailed Profiling 数据解析；
- `libQnnHtpOptraceProfilingReader.so`：Level 3 Optrace 数据解析。

当前部署规则：

1. `01_resource_push.py` 通过 `action_deploy()` 间接调用 `deploy_qnn_tools()`；
2. 优先使用 `qnn_sdk_root/lib/aarch64-android/` 中的 Reader；
3. SDK 中没有 Reader 时，才回退使用工具根目录的 `profile_lib/`；
4. Reader 被推送到 `device_qnn_root/profile_lib/`；
5. `qnn-net-run`、`qnn-profile-viewer`、QNN Android 库和 Reader 必须来自兼容的同一 SDK 版本，禁止随意混用。

`02_perf_validation.py` 不会再次推送 Reader，只会使用设备上已经部署的版本。当前 Level 2/3 执行脚本把 `device_qnn_root/profile_lib/` 加入 `LD_LIBRARY_PATH`；`qnn-profile-viewer` 调用没有显式传入 `--reader`。如果原始 Profiling Log 已生成但 CSV/Optrace 解析结果缺失，应优先检查 Reader 版本和 Viewer 的 Reader 加载方式。

## 配置文件

配置文件位于：

```text
models/<model-name>/profile-config.json
```

当前内置模型：

| 模型目录 | Context Length | 配置的 QNN SDK | 设备 QNN 目录 |
|---|---:|---|---|
| `qwen2.5-0.5b-instruct-cl4096` | 4096 | `vendor/qaisw-2.34` | `/data/local/tmp/qnn_htp_verify` |
| `qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21` | 512 | `vendor/qaisw-2.48` | `/data/local/tmp/qnn_htp_verify_248` |

相对路径统一相对于该 `profile-config.json` 所在目录解析。

### 配置示例

```json
{
  "project": "YourProject",
  "model_label": "your-model-label",
  "sdk_root": "../../vendor/qaisw-2.48",
  "qnn_sdk_root": "../../vendor/qaisw-2.48",
  "serial": "",
  "htp_arch": "73",
  "device_root": "/data/local/tmp/your_model_profile",
  "device_qnn_root": "/data/local/tmp/qnn_htp_verify_248",
  "context_binaries": [
    "part1_of_2.bin",
    "part2_of_2.bin"
  ],
  "part_mapping": {
    "part1": { "ctx_index": 0, "graph_suffix": "1_of_2" },
    "part2": { "ctx_index": 1, "graph_suffix": "2_of_2" }
  },
  "genie_config": "genie_config.json",
  "context_length": 512,
  "vocab_size": 151936,
  "num_layers": 28,
  "num_heads": 8,
  "kv_dim": 128,
  "hidden_size": 1024,
  "prompt": "What is gravity? Keep the answer under ten words.",
  "timeout_seconds": 240,
  "model_local_dir": "model",
  "tools_dir": "../../vendor/tools",
  "output_dir": ".",
  "levels": ["level1", "level2", "level3"],
  "app_compat": false
}
```

### 关键字段

| 字段 | 说明 |
|---|---|
| `sdk_root` | QAIRT/Genie SDK 根目录；未配置 `qnn_sdk_root` 时作为回退 |
| `qnn_sdk_root` | 当前实现优先使用的统一 Genie/QNN 工具链目录，避免跨版本混库 |
| `serial` | ADB 序列号；留空时自动选择唯一在线设备，多设备时必须填写 |
| `htp_arch` | HTP 架构编号，例如 V73 填写字符串 `"73"` |
| `device_root` | 模型、Genie runtime、DSP 库和输入的设备根目录 |
| `device_qnn_root` | QNN 工具、Android 库、DSP 库和 Profiling Reader 的设备根目录 |
| `context_binaries` | 参与 Profiling 的 Context Binary 文件名列表 |
| `part_mapping` | 文件顺序与 Graph part 不一致时，显式指定 `ctx_index` 和 `graph_suffix` |
| `context_length` | 从多 Graph Context Binary 中选择的目标 Context Length |
| `model_local_dir` | 本地模型资源目录，整个目录会被推送到 `device_root/model/` |
| `tools_dir` | `generate_qnn_synthetic_inputs.py` 所在工具目录 |
| `output_dir` | 本地结果根目录；相对路径相对于配置文件目录 |
| `levels` | `02_perf_validation.py` 实际执行的级别列表 |
| `app_compat` | 为固定读取平铺路径的 App 创建额外兼容副本，仅确有需要时启用 |

## 高级入口 `run_profile.py`

`run_profile.py` 保留为分步调试和兼容入口：

```powershell
# 完整旧式一键流程
python .\run_profile.py --config <config> --action all

# 单独部署
python .\run_profile.py --config <config> --action deploy

# 单独导出 Context 元数据
python .\run_profile.py --config <config> --action export-context

# 单独生成并推送合成输入
python .\run_profile.py --config <config> --action generate-inputs

# 单独执行某一级
python .\run_profile.py --config <config> --action level1
python .\run_profile.py --config <config> --action level2
python .\run_profile.py --config <config> --action level3

# 仅分析已有结果
python .\run_profile.py --config <config> --action analyze
```

`--action all` 为兼容流程：部署或 Context 导出失败时会记录错误并尝试使用既有资源继续；推荐的 `01_resource_push.py` 则会在部署阶段发生异常时直接失败，更适合确认资源完整性。

## 结果目录

当 `output_dir` 为 `.` 时，结果保存在对应模型目录：

```text
models/<model-name>/
├── profile-config.json
├── model/
├── context_info/
│   └── context_info/
├── qnn_inputs/
├── level1/
│   ├── genie-console.log
│   ├── genie-logcat.txt
│   └── genie-profile.json
├── level2/
│   └── detailed/
│       ├── prefill/part1|part2/
│       └── decode/part1|part2/
├── level3/
│   └── optrace/
│       ├── prefill/part1|part2/
│       └── decode/part1|part2/
└── reports/
    └── <timestamp>-performance-report.md
```

## 当前目录结构及作用

下面是 2026-08-21 当前实际目录的逻辑结构。大体积 raw 输入和每次运行产生的明细文件用占位符表示，避免目录树过长。

```text
qnn-htp-profiler/
├── 01_resource_push.cmd                 # Windows 交互入口：选择模型并执行资源部署
├── 01_resource_push.py                  # 阶段一：部署、导出 Context、生成并推送输入
├── 02_perf_validation.bat               # Windows 交互入口：选择模型并执行性能验证
├── 02_perf_validation.py                # 阶段二：Level 1/2/3 + 报告，不重新部署
├── run_profile.py                       # 底层编排入口，支持单独 action
├── README.md                            # 本文档
├── models/                              # 按模型隔离的配置、资源、输入和结果
│   ├── qwen2.5-0.5b-instruct-cl4096/
│   │   ├── profile-config.json          # Qwen2.5 Profiling 配置
│   │   ├── model/                       # Tokenizer、Genie 配置、HTP 配置、两个 ctx-bin
│   │   ├── context_info/
│   │   │   └── context_info/            # 从 ctx-bin 导出的 graph1.json、graph2.json
│   │   ├── qnn_inputs/                  # 按 Graph 生成的 raw 输入和 input_list.txt
│   │   ├── level1/                      # Genie 端到端 Profiling 结果
│   │   ├── level2/
│   │   │   └── detailed/                # QNN Detailed 的 prefill/decode × part1/part2
│   │   ├── level3/
│   │   │   └── optrace/                 # QNN Optrace 的 prefill/decode × part1/part2
│   │   └── reports/                     # 自动生成的 Markdown 性能报告
│   └── qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21/
│       ├── profile-config.json          # Qwen3 Profiling 配置
│       ├── model/                       # Qwen3 Genie bundle、Tokenizer 和两个 ctx-bin
│       ├── dsp/                         # 该模型留存的 V73 DSP/HTP 参考库
│       ├── context_info/context_info/   # 从多 Graph ctx-bin 导出的元数据
│       ├── qnn_inputs/                  # CL256/512/1024 的 Prefill/Decode 合成输入
│       ├── level1/                      # Genie 端到端 Profiling 结果
│       ├── level2/detailed/             # QNN Detailed 结果
│       ├── level3/optrace/              # QNN Optrace 结果
│       └── reports/                     # 自动生成的 Markdown 性能报告
├── profile_lib/                         # SDK Reader 不可用时的本地回退目录
│   ├── libQnnHtpProfilingReader.so      # Detailed Profiling Reader（当前为 QNN 2.34）
│   └── libQnnHtpOptraceProfilingReader.so # Optrace Reader（当前为 QNN 2.34）
├── scripts/                             # Python 核心实现
│   ├── __init__.py                      # Python package 标识
│   ├── profile_common.py                # 配置、ADB、日志、路径和设备公共函数
│   ├── run_level1_genie.py              # Genie runtime 部署与 Level 1 执行
│   ├── run_level2_detailed.py           # QNN 工具部署、Detailed/Optrace 公共执行逻辑
│   ├── run_level3_optrace.py            # Level 3 包装层，调用 Level 2 公共逻辑
│   └── analyze_results.py               # 汇总三级结果并生成报告
├── vendor/                              # 随工具保存的版本化依赖，不依赖系统全局 SDK
│   ├── qaisw-2.34/
│   │   ├── bin/aarch64-android/         # 2.34 的 qnn-net-run、viewer、context utility 等
│   │   └── lib/
│   │       ├── aarch64-android/         # 2.34 Android QNN 库和 Profiling Reader
│   │       └── hexagon-v73/unsigned/    # 2.34 V73 DSP/Skel 库
│   ├── qaisw-2.46/                      # 与上面相同布局的 2.46 工具和运行库
│   ├── qaisw-2.48/                      # 与上面相同布局的 2.48 工具和运行库
│   └── tools/
│       └── generate_qnn_synthetic_inputs.py # 根据 Graph JSON 生成 native raw 输入
└── __pycache__/                         # Python 自动生成的字节码缓存，可安全删除
```

### 顶层目录职责

| 目录 | 输入/生成属性 | 具体作用 |
|---|---|---|
| `models/` | 输入与生成物混合 | 每个子目录代表一套独立模型测试环境。模型配置和模型文件是输入；`context_info`、`qnn_inputs`、`level*`、`reports` 是脚本生成物。 |
| `profile_lib/` | 版本化输入 | 保存 Profiling Reader 回退副本。部署时优先使用 `vendor/qaisw-<version>/lib/aarch64-android/` 中的同版本 Reader，此目录仅在 SDK Reader 缺失时使用。 |
| `scripts/` | 源码 | 实现部署、设备执行、结果拉取和报告分析。入口脚本只负责编排，实际工作主要发生在这里。 |
| `vendor/` | 版本化输入 | 保存裁剪后的 QAIRT/QNN 多版本工具链和输入生成器，使不同模型可选择不同 SDK，同时避免依赖外部机器路径。 |
| `__pycache__/` | 自动生成 | Python 导入模块时生成的缓存，不参与功能逻辑，删除后会自动重建。 |

### 单个模型目录职责

| 相对路径 | 产生阶段 | 具体作用 |
|---|---|---|
| `profile-config.json` | 人工维护 | 唯一的模型测试配置入口，决定 SDK、设备目录、ctx-bin、Context Length、维度参数、执行级别和输出位置。 |
| `model/` | 人工准备 | 原始部署资源。阶段一会把整个目录推送到 `device_root/model/`，通常包含 Genie 配置、Tokenizer、HTP 扩展配置和 Context Binary。 |
| `dsp/` | 人工准备/参考 | 模型随附或验证过的 DSP/HTP 库归档。当前通用部署逻辑主要从配置指定的 `vendor/qaisw-*` 解析 DSP 库，因此此目录不是所有模型的必需目录。 |
| `context_info/context_info/` | `01` 的 `export-context` | 保存 `qnn-context-binary-utility` 从各 ctx-bin 导出的 `graph*.json`，后续输入生成和 Graph 选择依赖这些文件。 |
| `qnn_inputs/` | `01` 的 `generate-inputs` | 保存每个 Graph 的 `.raw` 输入、`input_list.txt` 和总清单 `manifest.json`，并同步到设备 `device_root/qnn_inputs/`。 |
| `level1/` | `02` 的 Level 1 | 保存 `genie-profile.json`、控制台日志、Logcat 和实际 Prompt，反映 Genie 端到端性能。 |
| `level2/detailed/` | `02` 的 Level 2 | 按 Prefill/Decode 和 Part 拆分保存 QNN 原始 Profiling Log、输出文件、`profile.csv` 与单次运行日志。 |
| `level3/optrace/` | `02` 的 Level 3 | 按 Prefill/Decode 和 Part 拆分保存 Optrace 原始数据、解析结果和运行日志。 |
| `reports/` | `02` 的 Analyze | 保存带时间戳的综合性能报告；只有全部启用级别成功时，`02_perf_validation.py` 才会生成新报告。 |

### `vendor/qaisw-*` 子目录职责

| 相对路径 | 具体作用 |
|---|---|
| `bin/aarch64-android/` | Android ARM64 可执行工具，包括 `genie-t2t-run`、`qnn-net-run`、`qnn-context-binary-utility` 和 `qnn-profile-viewer`。实际文件因 SDK 版本而略有不同。 |
| `lib/aarch64-android/` | Android ARM64 运行库、HTP Stub、Backend Extensions、System 库以及该 SDK 对应的 Profiling Reader。 |
| `lib/hexagon-v73/unsigned/` | Hexagon V73 DSP 侧运行库和 Skel，由 FastRPC/CDSP 加载；必须与 Android 侧 QNN 库保持版本兼容。 |

### 生成物与可复用资源边界

- 应长期保留：入口脚本、`scripts/`、`vendor/`、`profile_lib/`、各模型的 `profile-config.json` 和 `model/`。
- 可重新生成：`context_info/`、`qnn_inputs/`、`level1/`、`level2/`、`level3/`、`reports/` 和 `__pycache__/`。
- 更换 ctx-bin 后，旧 `context_info/` 和 `qnn_inputs/` 不应继续复用，应重新执行 `01_resource_push.py`。
- 只调整 Prompt 或重复采样时，可保留设备资源，直接重复执行 `02_perf_validation.py`。

## 前置条件

1. Windows 已安装 Python 3.10 或更高版本；
2. `adb` 已加入 `PATH`；
3. 目标设备已连接且状态为 `device`；
4. 配置引用的 SDK、模型和工具目录存在；
5. Context Binary、Tokenizer、Genie 配置与 QNN/Genie runtime 版本兼容；
6. 设备具备对应 HTP 架构和可用的 FastRPC/CDSP 环境；
7. 设备有足够空间保存模型、DSP/Android 库、合成输入和 Profiling 输出。

## 注意事项

- 不要混用不同 QAIRT/QNN 版本的 `genie-t2t-run`、`libGenie.so`、`libQnnHtp.so`、Stub、Skel、Viewer 和 Reader。
- `01_resource_push.py` 会清理 `device_root`，但不会清理配置中的其他设备路径。
- `device_qnn_root` 应按工具链版本隔离，防止不同模型互相覆盖 QNN 工具和 Reader。
- Level 2/3 数据适合定位 Graph/算子热点，不等同于 App 端到端真实性能。
- 正式性能结论应关闭 Detailed/Optrace，进行预热和多轮采样，并报告 P50/P90/P99。
- Context Binary 文件名与内部 Graph part 编号可能相反，应以导出的 Graph 名称和 `part_mapping` 为准。
- 如果设备上的 QNN/FastRPC 调用进入不可中断状态，普通超时或 `kill` 可能无法立即回收进程；在共享设备上执行测试前应先确认工具链和模型匹配。
