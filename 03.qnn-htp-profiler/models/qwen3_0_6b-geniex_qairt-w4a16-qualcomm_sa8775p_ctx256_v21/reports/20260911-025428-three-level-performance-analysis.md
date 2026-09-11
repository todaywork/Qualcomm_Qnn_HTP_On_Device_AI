# qwen3-0.6b-geniex_qairt-w4a16-sa8775p HTP 三级性能分析整合报告

- 分析日期：2026-09-11 02:54:28
- 项目：Qwen3-0.6B-HTP-Profile
- 模型资源标识：`qwen3-0.6b-geniex_qairt-w4a16-sa8775p`
- 模型参数：28 层 / 8 heads / hidden 1024 / KV dim 128 / 词表 151,936 / context 512
- QAIRT/QNN Runtime（CSV 记录）：v2.48.40.260702151143
- 测试设备：HONOR MAA-AN10（kalama），arm64-v8a
- 设备序列号：AN3GUT4220011858
- 数据目录：`E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21`
- Level 2 参数：`--profiling_level detailed --perf_profile burst --num_inferences 1`
- Level 3 参数：在 Level 2 基础上增加 `--profiling_option optrace`
- 报告结构：沿用 20260820-143407-three-level-performance-analysis.md；数据与结论重新计算。

## 1. 结论摘要

1. Genie 端到端：TTFT 33.650 ms，Prefill 1,517.451 tokens/s，Decode 52.706 tokens/s。
2. Level 2 两 part 加速器耗时合计：Prefill 109.718 ms，Decode 52.309 ms/token。
3. Level 2 加速器时间估算：Prefill 1,166.627 tokens/s（128 token 整图），Decode 19.117 tokens/s。
4. Detailed/Optrace 用于定位热点；单次采集不能证明稳定性能，也不能替代真实 App 的测速。

数据完整性：L2/L3 四个组合均取得 NETRUN EXECUTE 记录。

## 2. 模型与图结构

| 元数据文件 | 实际图名 | 输入（节选） | 输出（节选） |
|---|---|---|---|
| graph1.json | prompt_ar128_cl512_1_of_2 | input_ids [1, 128] | _model_model_embed_tokens_Gather_output_0 [1, 128, 1024] |
| graph1.json | token_ar1_cl512_1_of_2 | input_ids [1, 1] | _model_model_embed_tokens_Gather_output_0 [1, 1, 1024] |
| graph2.json | prompt_ar128_cl512_2_of_2 | past_key_0_in [8, 1, 128, 384]; past_value_0_in [8, 1, 384, 128]; past_key_1_in [8, 1, 128, 384]；共 60 项 | logits [1, 128, 151936]; past_key_0_out [8, 1, 128, 128]; past_value_0_out [8, 1, 128, 128]；共 57 项 |
| graph2.json | token_ar1_cl512_2_of_2 | past_key_0_in [8, 1, 128, 511]; past_value_0_in [8, 1, 511, 128]; past_key_1_in [8, 1, 128, 511]；共 60 项 | logits [1, 1, 151936]; past_key_0_out [8, 1, 128, 1]; past_value_0_out [8, 1, 1, 128]；共 57 项 |

真实生成由 Genie 连接模型分片、KV cache 与采样流程。Level 2/3 使用合成 raw 输入分别运行各图，不是完整自然语言生成。part 与 binary 的对应以 graph name、tensor I/O 和 part_mapping 为准，不能仅按文件后缀判断。

## 3. Level 1：Genie 端到端 Profiling

数据来源：`level1/genie-profile.json`。包含 Genie 调度和生成链路，仍需在实际 App 中复测。

### 会话：dialog0

| 指标 | 数值 | 含义 |
|---|---|---|
| 初始化耗时 | 1,383.322 ms | Genie 记录的初始化时间 |
| 会话创建总耗时 | 1,397.347 ms | create 事件持续时间 |
| 输入 token 数 | 51.000 | 本次输入长度 |
| 首 token 延迟（TTFT） | 33.650 ms | Genie 记录的首 token 时间 |
| Prefill 速度 | 1,517.451 tokens/s | 输入处理吞吐 |
| 生成 token 数 | 14.000 | 本次输出长度 |
| Decode 速度 | 52.706 tokens/s | 逐 token 生成吞吐 |
| 整个输入语料 Prefill 耗时（反算） | 33.609 ms | 全部输入 token 数 ÷ Prefill tokens/s；包括实际输入中的系统提示词、用户提示词及模板 token |
| 本次输出 Decode 耗时 | 246.651 ms | Genie 原始 token-generation-time；按 Genie 生成阶段口径，不用输出总 token 数除速度代替 |
| 整体推理耗时（不含初始化） | 281.065 ms | GenieDialog_query 持续时间；本次完整查询耗时 |

耗时口径：Prefill 耗时由吞吐反算，并非独立计时字段；Decode 耗时取原始生成计时。整体推理耗时取 query 原始记录，包含查询内其他处理开销，不强行用 Prefill + Decode 求和。整体耗时不含模型初始化、会话创建/释放、ADB 传输和 App 界面耗时。这里的“整个语料”指本次查询的完整输入，不是多条语料批次。

### 本次推理输入与输出

提示词和生成文本取自本次 `level1/genie-console.log`，不使用可能已被修改的配置倒推历史输入。

**推理前：系统提示词**

```text
You are a multilingual vehicle control assistant. Extract intent and slots from user commands in any language. Always respond with English JSON only. Do not think or explain.
```

**推理前：用户提示词**

```text
close the sunroof
```

**推理后：文本结果**

```text
{"intent":"CLOSE_SUNROOF","slot":{}}
```

**推理后：输出 token 数量**

dialog0：14 tokens（Genie 原始字段 `num-generated-tokens`；不是字符数或单词数）。

## 4. Level 2：QNN Detailed Profiling

### 4.1 性能汇总

| 阶段 | 分区 | NETRUN EXECUTE | Accelerator EXECUTE | Accelerator cycles |
|---|---|---|---|---|
| prefill 128 tokens | part1 | 4.196 ms | 2.900 ms | 28,391.000 |
| prefill 128 tokens | part2 | 1,603.587 ms | 106.818 ms | 131,196,165.000 |
| decode 1 token | part1 | 5.053 ms | 3.096 ms | 8,771.000 |
| decode 1 token | part2 | 1,346.626 ms | 49.213 ms | 47,946,716.000 |

### 4.2 为什么不能把 wall time 当推理性能

NETRUN EXECUTE 是执行调用的墙钟耗时；Accelerator EXECUTE 是后端报告的加速器耗时。两者差值可能包含 RPC、等待和 profiling 等开销，不能只凭差值确定原因。初始化与整个进程耗时也不能混为同一口径。

prefill：NETRUN 两 part 合计 1,607.783 ms；加速器合计 109.718 ms。吞吐估算 = 128 / 加速器合计秒数 = 1,166.627 tokens/s。

decode：NETRUN 两 part 合计 1,351.679 ms；加速器合计 52.309 ms。吞吐估算 = 1 / 加速器合计秒数 = 19.117 tokens/s。

128 是当前采集脚本的 Prefill AR，不能用 context length 替代。分片独立运行时间的求和仅为诊断估算，不代表 Genie 实测速度，也不是 TOPS。

### 4.3 part2 热点：Prefill

| 算子类型 | 数量 | Cycles | 占比 |
|---|---|---|---|
| Other | 3951 | 33,785,508 | 25.75% |
| MatMul | 1067 | 30,124,264 | 22.96% |
| Output | 1 | 19,682,170 | 15.00% |
| Softmax | 448 | 11,582,911 | 8.83% |
| Mul | 3108 | 10,305,552 | 7.86% |
| Conv | 952 | 8,553,210 | 6.52% |
| Slice | 1763 | 6,020,145 | 4.59% |
| Transpose | 560 | 5,334,981 | 4.07% |
| Add | 1120 | 5,213,177 | 3.97% |
| Concat | 1204 | 543,119 | 0.41% |

单算子 Top 5：

| 排名 | 算子 | Cycles | 占比 |
|---|---|---|---|
| 1 | `Output` | 19,682,170 | 15.00% |
| 2 | `/model/lm_head/MatMul` | 13,958,224 | 10.64% |
| 3 | `/model/model/layers.27/mlp/act_fn/Mul` | 298,714 | 0.23% |
| 4 | `/model/model/layers.12/mlp/act_fn/Mul` | 286,206 | 0.22% |
| 5 | `/model/model/layers.6/mlp/act_fn/Mul` | 285,206 | 0.22% |

本阶段观测到的首要算子类别是 Other。占比分母为已采集 SUB-EVENT cycles 之和；算子可能融合或重叠，不能直接解释为端到端耗时占比。是否未融合、是否存在无效搬运，需要结合图结构验证。

### 4.4 part2 热点：Decode

| 算子类型 | 数量 | Cycles | 占比 |
|---|---|---|---|
| MatMul | 1010 | 20,008,608 | 41.73% |
| Slice | 1763 | 8,812,236 | 18.38% |
| Other | 3279 | 5,308,483 | 11.07% |
| Conv | 952 | 4,735,470 | 9.88% |
| Mul | 3108 | 2,993,676 | 6.24% |
| Add | 1120 | 2,319,709 | 4.84% |
| Transpose | 560 | 1,461,506 | 3.05% |
| Softmax | 448 | 1,108,588 | 2.31% |
| Concat | 1204 | 697,198 | 1.45% |
| Output | 1 | 490,946 | 1.02% |

单算子 Top 5：

| 排名 | 算子 | Cycles | 占比 |
|---|---|---|---|
| 1 | `/model/lm_head/MatMul` | 6,643,530 | 13.86% |
| 2 | `Output` | 490,946 | 1.02% |
| 3 | `/model/model/layers.20/mlp/down_proj/Conv` | 101,355 | 0.21% |
| 4 | `/model/model/layers.18/mlp/down_proj/Conv` | 94,128 | 0.20% |
| 5 | `/model/model/layers.25/mlp/down_proj/Conv` | 93,403 | 0.19% |

本阶段观测到的首要算子类别是 MatMul。占比分母为已采集 SUB-EVENT cycles 之和；算子可能融合或重叠，不能直接解释为端到端耗时占比。是否未融合、是否存在无效搬运，需要结合图结构验证。

## 5. Level 3：Optrace Profiling

### 5.1 与 Level 2 对比

| 阶段 | 分区 | L2 NETRUN | L3 NETRUN | L2 Accel | L3 Accel | L2 cycles | L3 cycles |
|---|---|---|---|---|---|---|---|
| prefill | part1 | 4.196 ms | 15.204 ms | 2.900 ms | 3.281 ms | 28,391.000 | 69,492.000 |
| prefill | part2 | 1,603.587 ms | 1,522.845 ms | 106.818 ms | 85.891 ms | 131,196,165.000 | 113,085,922.000 |
| decode | part1 | 5.053 ms | 13.771 ms | 3.096 ms | 3.041 ms | 8,771.000 | 7,470.000 |
| decode | part2 | 1,346.626 ms | 1,390.880 ms | 49.213 ms | 40.421 ms | 47,946,716.000 | 40,793,215.000 |

prefill 加速器合计 L3 相对 L2 变化：-18.73%。

decode 加速器合计 L3 相对 L2 变化：-16.91%。

Optrace 的价值是逐算子轨迹与时序分析。不能预设它与 Detailed 完全一致，也不能用单次结果认定硬件计算量稳定。

## 6. 三级数据互证

| 维度 | Level 1（端到端） | Level 2 加速器估算 | Level 3 加速器估算 |
|---|---|---|---|
| decode | 52.706 tokens/s | 19.117 tokens/s | 23.009 tokens/s |
| prefill | 1,517.451 tokens/s | 1,166.627 tokens/s | 1,435.428 tokens/s |

Level 1 输入 51.000 tokens，Level 2/3 Prefill 固定 128 tokens；输入和采集开销不同，不宜直接比较。Decode 也需核对 KV 长度、频率和运行条件，不能仅因数值接近就认定上层开销很小。

## 7. 优化建议（按优先级）

### P0：先保证采集数据完整

检查原始 EXECUTE 事件、Reader 与运行库版本。缺失指标显示不可用；不根据空数据给优化收益。

### P0：检查 Prefill 输出与 Attention 热点

若元数据确认输出全序列 logits 且业务只需最后位置，可评估 LM Head 前截取最后 token。若 Softmax/MatMul 占比高，结合图结构检查 Attention 融合；仅凭算子数量不能证明未融合。

### P1：评估 context 长度与 KV cache

当前 context 为 512；按业务长度评估多套图，记录精度、速度和内存的变化。

### P1：减少分片调用与拷贝

在真实 Genie 流程中验证 context 常驻、共享 buffer 和重复加载情况，再做 A/B 测试。

### P2：核对模型生成与运行库版本

采用受支持的版本组合，重新生成模型后验证兼容性、精度和性能；不直接套用历史环境的结论。

## 8. 正式测速方法建议

1. 使用低开销 profiling 建立速度基准，Detailed/Optrace 用于诊断。
2. 同一进程常驻加载模型分片。
3. 建议预热 5–10 次，至少采集 50 个可比样本，报告 P50/P90/P99 与失败率。
4. 分别记录初始化、TTFT、Prefill/Decode 速度、峰值内存及持续温度。
5. 使用真实 App 完成输入、生成、取消和生命周期测试。
6. A/B 保持模型、prompt、context、采样配置和设备条件一致。
7. 当前脚本未持续采集内存、温度、NPU 利用率或进程级 TOPS，这些指标需另行补齐。

## 附录：数据文件位置

| 来源 | 路径 | 修改时间 |
|---|---|---|
| Level 1 | E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\level1\genie-profile.json | 2026-09-11T02:54:01 |
| Level 2 prefill/part1 | E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\level2\detailed\prefill\part1\output\profile.csv | 2026-09-11T02:54:03 |
| Level 2 prefill/part2 | E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\level2\detailed\prefill\part2\output\profile.csv | 2026-09-11T02:54:08 |
| Level 2 decode/part1 | E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\level2\detailed\decode\part1\output\profile.csv | 2026-09-11T02:54:10 |
| Level 2 decode/part2 | E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\level2\detailed\decode\part2\output\profile.csv | 2026-09-11T02:54:14 |
| Level 3 prefill/part1 | E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\level3\optrace\prefill\part1\output\profile.csv | 2026-09-11T02:54:16 |
| Level 3 prefill/part2 | E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\level3\optrace\prefill\part2\output\profile.csv | 2026-09-11T02:54:21 |
| Level 3 decode/part1 | E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\level3\optrace\decode\part1\output\profile.csv | 2026-09-11T02:54:23 |
| Level 3 decode/part2 | E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\level3\optrace\decode\part2\output\profile.csv | 2026-09-11T02:54:27 |

复现命令（工具根目录执行）：

```powershell
python 02_perf_validation.py --config "E:\QualComm\qnn-htp-profiler\models\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx256_v21\profile-config.json"
```
