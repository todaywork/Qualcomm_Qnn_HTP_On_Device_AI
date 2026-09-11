# Qwen2.5-0.5B HTP 三级性能分析整合报告

- 分析日期：2026-08-20
- 模型资源标识：`qwen2.5-0.5b-instruct_qnn229_qcs8550_4096`（24 层 / 14 heads / hidden 896 / KV dim 64 / 词表 151,936 / context 4096）
- QAIRT/QNN Runtime：`v2.34.0.250424201103_119471-auto`
- 测试设备：gen4_gvm（SA8255P），arm64-v8a
- 数据目录：`models/qwen2.5-0.5b-instruct-cl4096/`（level1、level2、level3 子目录，本次全部为重跑后的新数据）
- Level 2 参数：`--profiling_level detailed --perf_profile burst --num_inferences 1`
- Level 3 参数：在 Level 2 基础上增加 `--profiling_option optrace`
- 参考文档：本目录下 `level2-detail-performance-analysis.md`、`level3-optrace-performance-analysis.md`（2026-07 在 QCS8550 上的历史分析，本文沿用其结构与结论框架，数据全部替换为本次 gen4_gvm 实测）

## 1. 结论摘要

1. 模型在 QNN 侧被拆成两个图（part）：part1 是 Token Embedding（仅一次 Gather），part2 是 24 层 Transformer + LM Head，承担了 99.9% 以上的计算量。
2. 每个 part 内含两种图：`ar128`（Prefill，一次 128 token）和 `ar1`（Decode，一次 1 token）。
3. **端到端（Level 1）**：Init 约 2.0 s，TTFT 约 150 ms，Prefill 40.0 tokens/s，Decode 22.0 tokens/s（32 token 生成）。
4. **硬件侧（Level 2/3）**：Prefill accelerator 约 146.6 ms（两 part 合计），Decode 约 44.4 ms/token；按 accelerator 时间粗算 Prefill ≈ 873 tokens/s、Decode ≈ 22.5 tokens/s，与 Level 1 的 Decode 22.0 tokens/s 高度吻合，三级数据互相印证。
5. 最大热点：Prefill 的 336 个逐 head Softmax（占 part2 cycles 35.3%）；Prefill 输出完整 `[1,128,151936]` logits（Output Op 占 11.1%）；Decode 的 LM Head 单算子占 6.75%。
6. Level 2 的 NETRUN wall time 远大于 accelerator 时间（detailed profiling 的逐算子采集 + RPC 开销），不能直接当端到端性能看。

## 2. 模型与图结构

| 分区 | 功能 | 输入 | 输出 |
|---|---|---|---|
| part1 | Token Embedding | `input_ids` | `[batch, seq, 896]` embedding |
| part2 | 24 层 Transformer + LM Head | embedding、attention mask、KV cache | logits、新 KV cache |

真实链路（part2 的 embedding 来自 part1 的输出）：

```text
Prefill: input_ids[1,128] → part1(Embedding) → embedding[1,128,896]
         → part2(24层 Decoder + LM Head) → logits[1,128,151936] + KV cache

Decode:  input_ids[1,1]   → part1(Embedding) → embedding[1,1,896]
         → part2(24层 Decoder + LM Head) → logits[1,1,151936] + KV cache
```

注意命名反转（历史遗留）：文件名 `..._1_of_2.serialized.bin` 内部装的是 `..._2_of_2` 的图，反之亦然。部署/脚本不要按文件后缀判断 part，要以 graph name 和 tensor I/O 为准（当前脚本已从 context_info 元数据解析真实图名）。

## 3. Level 1：Genie 端到端 Profiling

数据来源：`level1/genie-profile.json`（genie-t2t-run，等价于 App 内推理链路）。

| 指标 | 数值 |
|---|---:|
| Init time | 2015.164 ms |
| Dialog create total | 2015.334 ms |
| Prompt tokens | 6 |
| Time to first token (TTFT) | 149.969 ms |
| Prefill rate | 40.010 toks/s |
| Generated tokens | 32 |
| Decode rate | 22.009 toks/s |

说明：

- Init 约 2 秒，主要是 context binary 加载与 HTP 会话建立（`use-mmap: true` 已生效）。
- 这是唯一包含 tokenizer、sampler、Genie 调度等完整链路的指标，最接近 App 真实体验。
- Level 1 的 Decode 22.0 tokens/s 与 Level 2/3 硬件侧推算的 22.5 tokens/s 基本一致，说明 Genie 上层引入的额外开销很小。

## 4. Level 2：QNN Detailed Profiling

数据来源：`level2/detailed/{prefill,decode}/{part1,part2}/output/profile.csv`。

### 4.1 性能汇总

| 阶段 | 分区 | NETRUN EXECUTE | Accelerator EXECUTE | Accelerator cycles |
|---|---|---:|---:|---:|
| Prefill 128 tokens | part1 | 11.861 ms | 4.153 ms | 24,717 |
| Prefill 128 tokens | part2 | 1116.629 ms | 142.467 ms | 150,235,185 |
| Decode 1 token | part1 | 11.869 ms | 4.098 ms | 2,924 |
| Decode 1 token | part2 | 754.884 ms | 40.259 ms | 31,949,136 |

part1 计算量极小（Prefill 占总 accelerator cycles 约 0.016%，Decode 约 0.009%），但作为独立 QNN 图调用仍有约 12 ms 的固定开销（RPC、profiling、唤醒）。性能几乎完全由 part2 决定。

### 4.2 为什么不能把 wall time 当推理性能

```text
Prefill：11.861 + 1116.629 ≈ 1128.5 ms
Decode： 11.869 + 754.884  ≈ 766.8 ms/token
```

这组数字是 `--profiling_level detailed` + `--num_inferences 1` + 每 part 单独起进程 + 输出落盘的结果。以 part2 为例：Decode 的 NETRUN execute 754.9 ms 中 accelerator 只占 40.3 ms，其余是逐算子采集与 RPC 同步开销。

按 accelerator execute 粗算 HTP 计算能力（不含 Genie 上层）：

```text
Prefill：128 / (0.004153 + 0.142467) ≈ 873 tokens/s
Decode：  1 / (0.004098 + 0.040259) ≈ 22.5 tokens/s
```

### 4.3 part2 热点：Prefill

| 算子类型 | 数量 | Cycles | 占比 |
|---|---:|---:|---:|
| Softmax | 336 | 53,031,750 | 35.30% |
| Other | 682 | 27,018,485 | 17.98% |
| Add | 791 | 22,782,337 | 15.16% |
| Conv | 578 | 17,426,442 | 11.60% |
| MatMul | 672 | 14,587,277 | 9.71% |
| Mul | 1,561 | 11,985,513 | 7.98% |
| Slice | 864 | 2,609,727 | 1.74% |
| Concat | 552 | 458,551 | 0.31% |
| Transpose | 169 | 335,103 | 0.22% |

单算子 Top 5：

| 排名 | 算子 | Cycles | 占比 |
|---:|---|---:|---:|
| 1 | `Output`（全量 logits 输出） | 16,691,368 | 11.11% |
| 2 | `lm_head_conv_Conv` | 7,570,645 | 5.04% |
| 3 | `model_layers_9_mlp_act_fn_Mul` | 407,329 | 0.27% |
| 4 | `model_layers_8_mlp_act_fn_Mul` | 404,290 | 0.27% |
| 5 | `model_layers_2_mlp_act_fn_Mul` | 402,551 | 0.27% |

两个明显问题：

1. **336 个 Softmax = 24 层 × 14 heads**：attention 被拆成逐 head 的 Softmax/MatMul，没有形成融合 attention，是 Prefill 最大热点（35.3%）。
2. **Prefill 输出全部 128 个位置的 logits** `[1,128,151936]`：自回归生成只需要最后一个 token 的 logits，白白多了约 37 MiB 输出搬运（Output Op 占 11.11%）和多余的 LM Head 计算（5.04%）。

### 4.4 part2 热点：Decode

| 算子类型 | 数量 | Cycles | 占比 |
|---|---:|---:|---:|
| MatMul | 672 | 10,476,129 | 32.79% |
| Conv | 578 | 6,722,782 | 21.04% |
| Add | 791 | 6,201,045 | 19.41% |
| Softmax | 336 | 4,113,177 | 12.87% |
| Slice | 864 | 1,788,239 | 5.60% |
| Mul | 1,561 | 1,131,901 | 3.54% |
| Other | 679 | 1,041,491 | 3.26% |
| Concat | 552 | 443,566 | 1.39% |
| Transpose | 169 | 30,806 | 0.10% |

单算子 Top 5：

| 排名 | 算子 | Cycles | 占比 |
|---:|---|---:|---:|
| 1 | `lm_head_conv_Conv` | 2,156,146 | 6.75% |
| 2 | `attn_0_head_0_qk_MatMul_0` | 147,546 | 0.46% |
| 3 | `attn_1_head_0_qk_MatMul_0` | 133,773 | 0.42% |
| 4 | `attn_10_head_0_qk_MatMul_0` | 133,657 | 0.42% |
| 5 | `attn_5_head_0_qk_MatMul_0` | 133,633 | 0.42% |

Decode 主要受限因素：每层 attention 的 QK MatMul、LM Head 对 151,936 词表的投影、固定 4096 context 下的 KV cache 读写、大量逐 head 算子调度。

## 5. Level 3：Optrace Profiling

数据来源：`level3/optrace/{prefill,decode}/{part1,part2}/output/`（`profile.csv` + `profile-from-optrace.csv`）。

### 5.1 与 Level 2 对比

| 阶段 | 分区 | L2 NETRUN | L3 NETRUN | L2 Accel | L3 Accel | L2 cycles | L3 cycles |
|---|---|---:|---:|---:|---:|---:|---:|
| Prefill | part1 | 11.861 ms | 25.739 ms | 4.153 ms | 4.160 ms | 24,717 | 24,483 |
| Prefill | part2 | 1116.629 ms | 1136.218 ms | 142.467 ms | 142.270 ms | 150,235,185 | 154,344,157 |
| Decode | part1 | 11.869 ms | 25.752 ms | 4.098 ms | 4.082 ms | 2,924 | 2,880 |
| Decode | part2 | 754.884 ms | 782.416 ms | 40.259 ms | 40.162 ms | 31,949,136 | 31,983,095 |

结论：

- optrace 使 NETRUN wall time 进一步增加（part1 这种小图翻倍，part2 约 +2~4%），**accelerator 时间基本不变**（差异 <0.6%）。
- 硬件计算量在两种 profiling 级别下稳定一致，L2/L3 对热点的判断相同。
- Level 3 的价值是逐算子轨迹（时序视角），不适合用来报真实延迟。

## 6. 三级数据互证

| 维度 | Level 1（端到端） | Level 2/3 推算（accelerator） |
|---|---:|---:|
| Decode | 22.0 tokens/s | ≈ 22.5 tokens/s |
| Prefill | 40.0 tokens/s（6 token 短 prompt） | ≈ 873 tokens/s（128 token 整图） |

Decode 两侧几乎一致，说明：Decode 场景下 Genie/采样/调度开销很小，瓶颈在 HTP 计算本身；Prefill 的 Level 1 数值受 prompt 极短（6 token）影响，与整图 128 token 的 Level 2 不具直接可比性。

## 7. 优化建议（按优先级）

### P0：Prefill 在 LM Head 前只保留最后一个 token

当前输出完整 `[1,128,151936]` logits。改为：

```text
hidden_states [1,128,896] → Slice last token → [1,1,896] → LM Head → logits [1,1,151936]
```

预期收益：输出缩小 128 倍，消除 Output Op 约 11.1% 的 cycles，LM Head 只算一个位置再省约 5%。

### P0：恢复/增强 Attention 融合

336 个逐 head Softmax + 672 个 MatMul 表明 attention 未融合。建议检查导出流程中 reshape/transpose/slice/mask Add 是否阻断了 QNN 的 attention pattern matching，优先采用 Qualcomm 官方验证过的 LLM/Genie 导出 recipe。

### P1：降低固定 4096 context 对 Decode 的影响

按需生成 1024/2048/4096 多套图，运行时按真实上下文长度选图；评估动态有效长度/KV window 或 sliding-window 策略。

### P1：减少 part1 独立调用的固定开销

part1 计算仅数千~数万 cycles，但独立调用有毫秒级固定开销。正式链路中应让两个 context 常驻、part1 输出直接以共享 buffer 传给 part2，避免重复加载和 host copy；也可评估 embedding 并入 decoder 图（需权衡 context 体积与内存）。

### P2：统一模型生成版本

ctx bin 文件名标记 `qnn229`，运行时为 QAIRT 2.34。跨版本加载目前工作正常，但建议用 2.34 重新生成 context binary 做 A/B 测试，确认能吃到新版的图融合与调度优化。

## 8. 正式测速方法建议

当前数据用于定位算子热点，不用于发布端到端性能。正式测速建议：

1. 关闭 detailed/optrace，或使用 basic profiling。
2. 同一进程内常驻加载 part1、part2 context。
3. Warm-up 5~10 次后，连续执行至少 50 次 Decode，报告 P50/P90/P99。
4. 分别报告：context 初始化耗时、TTFT、Prefill tokens/s、Decode tokens/s、单 token P50/P90 延迟、峰值内存、持续运行温度与降频情况。
5. 用真实 Genie App 测 part1 → part2 → sampler 完整链路。
6. A/B 对比时保持相同 prompt、相同 context length、相同设备温度。

## 附录：数据文件位置

```text
models/qwen2.5-0.5b-instruct-cl4096/
├── level1/genie-profile.json                 # Level 1 端到端指标
├── level2/detailed/<stage>/<part>/output/profile.csv
├── level3/optrace/<stage>/<part>/output/profile.csv + profile-from-optrace.csv
└── reports/                                  # 每次 02_perf_validation.py 自动生成的汇总
```

复现命令（在 `E:\QualComm\qnn-htp-profiler\` 下）：

```bash
python 01_resource_push.py      # 环境变化后执行一次
python 02_perf_validation.py    # 每次性能验证
```
