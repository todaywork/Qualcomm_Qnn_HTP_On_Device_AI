# qwen2.5-0.5b-instruct_qnn229_qcs8550_4096 HTP 三级性能分析整合报告

- 分析日期：2026-09-11 02:28:05
- 项目：Qwen3GenieDemo
- 模型资源标识：`qwen2.5-0.5b-instruct_qnn229_qcs8550_4096`
- 模型参数：24 层 / 14 heads / hidden 896 / KV dim 64 / 词表 151,936 / context 4096
- QAIRT/QNN Runtime（CSV 记录）：v2.34.0.250424201103_119471-auto
- 测试设备：HONOR MAA-AN10（kalama），arm64-v8a
- 设备序列号：AN3GUT4220011858
- 数据目录：`E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096`
- Level 2 参数：`--profiling_level detailed --perf_profile burst --num_inferences 1`
- Level 3 参数：在 Level 2 基础上增加 `--profiling_option optrace`
- 报告结构：沿用 20260820-143407-three-level-performance-analysis.md；数据与结论重新计算。

## 1. 结论摘要

1. Genie 端到端：TTFT 38.403 ms，Prefill 156.250 tokens/s，Decode 77.202 tokens/s。
2. Level 2 两 part 加速器耗时合计：Prefill 109.504 ms，Decode 34.816 ms/token。
3. Level 2 加速器时间估算：Prefill 1,168.907 tokens/s（128 token 整图），Decode 28.722 tokens/s。
4. Detailed/Optrace 用于定位热点；单次采集不能证明稳定性能，也不能替代真实 App 的测速。

数据完整性：L2/L3 四个组合均取得 NETRUN EXECUTE 记录。

## 2. 模型与图结构

| 元数据文件 | 实际图名 | 输入（节选） | 输出（节选） |
|---|---|---|---|
| graph1.json | ar128_cl4096_2_of_2 | attention_mask [1, 1, 128, 4096]; past_key_3_in [2, 1, 64, 3968]; past_value_3_in [2, 1, 3968, 64]；共 52 项 | past_value_0_out [2, 1, 128, 64]; past_key_0_out [2, 1, 64, 128]; past_value_1_out [2, 1, 128, 64]；共 49 项 |
| graph1.json | ar1_cl4096_2_of_2 | attention_mask [1, 1, 1, 4096]; past_key_3_in [2, 1, 64, 4095]; past_value_3_in [2, 1, 4095, 64]；共 52 项 | past_value_0_out [2, 1, 1, 64]; past_key_0_out [2, 1, 64, 1]; past_value_1_out [2, 1, 1, 64]；共 49 项 |
| graph2.json | ar128_cl4096_1_of_2 | input_ids [1, 128] | _model_embed_tokens_Gather_Gather_output_0 [1, 128, 896] |
| graph2.json | ar1_cl4096_1_of_2 | input_ids [1, 1] | _model_embed_tokens_Gather_Gather_output_0 [1, 1, 896] |

真实生成由 Genie 连接模型分片、KV cache 与采样流程。Level 2/3 使用合成 raw 输入分别运行各图，不是完整自然语言生成。part 与 binary 的对应以 graph name、tensor I/O 和 part_mapping 为准，不能仅按文件后缀判断。

## 3. Level 1：Genie 端到端 Profiling

数据来源：`level1/genie-profile.json`。包含 Genie 调度和生成链路，仍需在实际 App 中复测。

### 会话：dialog0

| 指标 | 数值 | 含义 |
|---|---|---|
| 初始化耗时 | 1,410.156 ms | Genie 记录的初始化时间 |
| 会话创建总耗时 | 1,410.474 ms | create 事件持续时间 |
| 输入 token 数 | 6.000 | 本次输入长度 |
| 首 token 延迟（TTFT） | 38.403 ms | Genie 记录的首 token 时间 |
| Prefill 速度 | 156.250 tokens/s | 输入处理吞吐 |
| 生成 token 数 | 32.000 | 本次输出长度 |
| Decode 速度 | 77.202 tokens/s | 逐 token 生成吞吐 |

## 4. Level 2：QNN Detailed Profiling

### 4.1 性能汇总

| 阶段 | 分区 | NETRUN EXECUTE | Accelerator EXECUTE | Accelerator cycles |
|---|---|---|---|---|
| prefill 128 tokens | part1 | 7.596 ms | 3.057 ms | 49,433.000 |
| prefill 128 tokens | part2 | 1,246.042 ms | 106.447 ms | 158,290,504.000 |
| decode 1 token | part1 | 7.531 ms | 2.924 ms | 6,207.000 |
| decode 1 token | part2 | 874.715 ms | 31.892 ms | 34,707,730.000 |

### 4.2 为什么不能把 wall time 当推理性能

NETRUN EXECUTE 是执行调用的墙钟耗时；Accelerator EXECUTE 是后端报告的加速器耗时。两者差值可能包含 RPC、等待和 profiling 等开销，不能只凭差值确定原因。初始化与整个进程耗时也不能混为同一口径。

prefill：NETRUN 两 part 合计 1,253.638 ms；加速器合计 109.504 ms。吞吐估算 = 128 / 加速器合计秒数 = 1,168.907 tokens/s。

decode：NETRUN 两 part 合计 882.246 ms；加速器合计 34.816 ms。吞吐估算 = 1 / 加速器合计秒数 = 28.722 tokens/s。

128 是当前采集脚本的 Prefill AR，不能用 context length 替代。分片独立运行时间的求和仅为诊断估算，不代表 Genie 实测速度，也不是 TOPS。

### 4.3 part2 热点：Prefill

| 算子类型 | 数量 | Cycles | 占比 |
|---|---|---|---|
| Softmax | 336 | 52,632,812 | 33.25% |
| Conv | 578 | 24,260,573 | 15.33% |
| Add | 791 | 22,650,743 | 14.31% |
| Output | 1 | 17,655,142 | 11.15% |
| MatMul | 672 | 14,422,332 | 9.11% |
| Mul | 1561 | 12,194,470 | 7.70% |
| Other | 677 | 10,618,000 | 6.71% |
| Slice | 864 | 2,976,803 | 1.88% |
| Concat | 552 | 467,834 | 0.30% |
| Transpose | 169 | 410,348 | 0.26% |

单算子 Top 5：

| 排名 | 算子 | Cycles | 占比 |
|---|---|---|---|
| 1 | `Output` | 17,655,142 | 11.15% |
| 2 | `lm_head_conv_Conv` | 12,800,392 | 8.09% |
| 3 | `model_layers_0_mlp_act_fn_Mul` | 417,047 | 0.26% |
| 4 | `model_layers_19_mlp_act_fn_Mul` | 414,957 | 0.26% |
| 5 | `model_layers_8_mlp_act_fn_Mul` | 414,944 | 0.26% |

本阶段观测到的首要算子类别是 Softmax。占比分母为已采集 SUB-EVENT cycles 之和；算子可能融合或重叠，不能直接解释为端到端耗时占比。是否未融合、是否存在无效搬运，需要结合图结构验证。

### 4.4 part2 热点：Decode

| 算子类型 | 数量 | Cycles | 占比 |
|---|---|---|---|
| MatMul | 672 | 10,815,894 | 31.16% |
| Conv | 578 | 8,417,375 | 24.25% |
| Add | 791 | 6,191,229 | 17.84% |
| Softmax | 336 | 4,024,612 | 11.60% |
| Slice | 864 | 2,101,502 | 6.05% |
| Mul | 1561 | 1,328,613 | 3.83% |
| Other | 677 | 1,195,080 | 3.44% |
| Concat | 552 | 460,126 | 1.33% |
| Output | 1 | 130,580 | 0.38% |
| Transpose | 169 | 41,018 | 0.12% |

单算子 Top 5：

| 排名 | 算子 | Cycles | 占比 |
|---|---|---|---|
| 1 | `lm_head_conv_Conv` | 2,987,182 | 8.61% |
| 2 | `attn_12_head_0_qk_MatMul_0` | 171,618 | 0.49% |
| 3 | `attn_0_head_0_qk_MatMul_0` | 169,155 | 0.49% |
| 4 | `attn_2_head_0_qk_MatMul_0` | 153,937 | 0.44% |
| 5 | `attn_17_head_7_qk_MatMul_0` | 145,339 | 0.42% |

本阶段观测到的首要算子类别是 MatMul。占比分母为已采集 SUB-EVENT cycles 之和；算子可能融合或重叠，不能直接解释为端到端耗时占比。是否未融合、是否存在无效搬运，需要结合图结构验证。

## 5. Level 3：Optrace Profiling

### 5.1 与 Level 2 对比

| 阶段 | 分区 | L2 NETRUN | L3 NETRUN | L2 Accel | L3 Accel | L2 cycles | L3 cycles |
|---|---|---|---|---|---|---|---|
| prefill | part1 | 7.596 ms | 17.573 ms | 3.057 ms | 3.397 ms | 49,433.000 | 58,976.000 |
| prefill | part2 | 1,246.042 ms | 1,185.844 ms | 106.447 ms | 114.166 ms | 158,290,504.000 | 159,392,883.000 |
| decode | part1 | 7.531 ms | 17.356 ms | 2.924 ms | 3.101 ms | 6,207.000 | 9,327.000 |
| decode | part2 | 874.715 ms | 934.470 ms | 31.892 ms | 43.508 ms | 34,707,730.000 | 39,951,210.000 |

prefill 加速器合计 L3 相对 L2 变化：+7.36%。

decode 加速器合计 L3 相对 L2 变化：+33.87%。

Optrace 的价值是逐算子轨迹与时序分析。不能预设它与 Detailed 完全一致，也不能用单次结果认定硬件计算量稳定。

## 6. 三级数据互证

| 维度 | Level 1（端到端） | Level 2 加速器估算 | Level 3 加速器估算 |
|---|---|---|---|
| decode | 77.202 tokens/s | 28.722 tokens/s | 21.455 tokens/s |
| prefill | 156.250 tokens/s | 1,168.907 tokens/s | 1,088.778 tokens/s |

Level 1 输入 6.000 tokens，Level 2/3 Prefill 固定 128 tokens；输入和采集开销不同，不宜直接比较。Decode 也需核对 KV 长度、频率和运行条件，不能仅因数值接近就认定上层开销很小。

## 7. 优化建议（按优先级）

### P0：先保证采集数据完整

检查原始 EXECUTE 事件、Reader 与运行库版本。缺失指标显示不可用；不根据空数据给优化收益。

### P0：检查 Prefill 输出与 Attention 热点

若元数据确认输出全序列 logits 且业务只需最后位置，可评估 LM Head 前截取最后 token。若 Softmax/MatMul 占比高，结合图结构检查 Attention 融合；仅凭算子数量不能证明未融合。

### P1：评估 context 长度与 KV cache

当前 context 为 4096；按业务长度评估多套图，记录精度、速度和内存的变化。

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
| Level 1 | E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096\level1\genie-profile.json | 2026-09-11T02:27:45 |
| Level 2 prefill/part1 | E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096\level2\detailed\prefill\part1\output\profile.csv | 2026-09-11T02:27:46 |
| Level 2 prefill/part2 | E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096\level2\detailed\prefill\part2\output\profile.csv | 2026-09-11T02:27:50 |
| Level 2 decode/part1 | E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096\level2\detailed\decode\part1\output\profile.csv | 2026-09-11T02:27:52 |
| Level 2 decode/part2 | E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096\level2\detailed\decode\part2\output\profile.csv | 2026-09-11T02:27:54 |
| Level 3 prefill/part1 | E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096\level3\optrace\prefill\part1\output\profile.csv | 2026-09-11T02:27:56 |
| Level 3 prefill/part2 | E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096\level3\optrace\prefill\part2\output\profile.csv | 2026-09-11T02:27:59 |
| Level 3 decode/part1 | E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096\level3\optrace\decode\part1\output\profile.csv | 2026-09-11T02:28:02 |
| Level 3 decode/part2 | E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096\level3\optrace\decode\part2\output\profile.csv | 2026-09-11T02:28:05 |

复现命令（工具根目录执行）：

```powershell
python 02_perf_validation.py --config "E:\QualComm\qnn-htp-profiler\models\qwen2.5-0.5b-instruct-cl4096\profile-config.json"
```
