# Level 3（optrace）性能分析报告

- 分析日期：2026-07-23
- 数据目录：`D:\QAIRT-Workspace\android-apps\Qwen3GenieDemo\profile-results\level3`
- 对比目录：`D:\QAIRT-Workspace\android-apps\Qwen3GenieDemo\profile-results\level2`
- QAIRT/QNN Runtime：`v2.34.0.250424201103_119471-auto`
- 目标平台：QCS8550，HTP backend
- 模型资源标识：`qwen2.5-0.5b-instruct_qnn229_qcs8550_4096`
- Profiling 参数：`--profiling_level detailed --profiling_option optrace`
- 性能模式：`--perf_profile burst`
- 测量次数：`--num_inferences 1`

## 1. 结论摘要

1. Level 3 与 Level 2 使用的是同一个模型、相同输入形状和相同的两段式模型结构。Level 3 的区别是增加了 `--profiling_option optrace`。
2. 模型分成两个 part：
   - `part1`：Token Embedding，仅执行一次 Gather。
   - `part2`：24 层 Transformer、KV cache 处理、RMSNorm 和 LM Head，是几乎全部计算量所在。
3. 每个 context binary 内同时包含两种图：
   - `ar128`：Prefill，一次处理 128 tokens。
   - `ar1`：Decode，一次处理 1 token。
4. part1 的 accelerator cycles 占比极低：
   - Prefill：0.026%。
   - Decode：0.017%。
   但由于它被作为独立 QNN 图调用，仍产生约 16.5 ms 的 optrace wall time，主要是 RPC、profiling 和固定调用开销。
5. Level 3 的硬件计算结果与 Level 2 基本一致：
   - Prefill accelerator 时间变化约 +0.20%（两 part 合计）。
   - Decode accelerator 时间变化约 -1.34%（两 part 合计）。
   说明两次采样的硬件执行稳定，optrace 主要增加 host/RPC/profiling wall time，而没有显著改变 HTP 计算量。
6. 当前最大的模型级性能问题：
   - Prefill：336 个逐 head Softmax，占 part2 cycles 的 34.33%。
   - Prefill 输出完整 `[1,128,151936]` logits，单独的 Output Op 占 11.65%。
   - Decode：MatMul 占 31.27%，LM Head 单算子占 8.92%。
7. 当前数据不能直接作为 App 的端到端 token/s：
   - 使用 detailed + optrace。
   - 每个 part 单独启动一次 qnn-net-run。
   - 每次只执行 1 次 inference。
   - 包含大量逐算子采集、RPC 同步、冷启动及输出文件落盘影响。

## 2. 数据完整性检查

Level 3 的四个 `optrace.csv` 均为 0 字节：

```text
optrace/prefill/part1/output/optrace.csv
optrace/prefill/part2/output/optrace.csv
optrace/decode/part1/output/optrace.csv
optrace/decode/part2/output/optrace.csv
```

实际可用的解析结果位于各目录的 `profile-from-optrace.csv`。这些文件数据完整：

| 阶段 | 分区 | CSV 行数 | SUB-EVENT 数 | Root cycles | 子事件 cycles 合计 | 差值 |
|---|---:|---:|---:|---:|---:|---:|
| Prefill | part1 | 28 | 3 | 40,641 | 40,641 | 0 |
| Prefill | part2 | 6,227 | 6,202 | 153,878,355 | 153,878,355 | 0 |
| Decode | part1 | 28 | 3 | 5,572 | 5,572 | 0 |
| Decode | part2 | 6,227 | 6,202 | 33,729,756 | 33,729,756 | 0 |

因此，空的 `optrace.csv` 不影响本报告的逐算子 cycles 分析；`profile-from-optrace.csv` 已经包含完整的 optrace 解析结果。

每个 profile 中还存在一条：

```text
Unit of Measurement: OBJECT
Event Identifier: Unknown event type
```

该事件没有数值，未计入任何耗时或 cycles 汇总。全部有数值的子事件仍能与 root cycles 精确闭合。

## 3. 模型与分区结构

### 3.1 part1：Token Embedding

Prefill 图：

```text
input_ids: INT32 [1,128]
    ↓ model_embed_tokens_Gather
embedding: UFIXED_POINT_16 [1,128,896]
```

Decode 图：

```text
input_ids: INT32 [1,1]
    ↓ model_embed_tokens_Gather
embedding: UFIXED_POINT_16 [1,1,896]
```

part1 只有 3 个事件：Input、Gather、Output。

| 阶段 | Input cycles | Gather cycles | Output cycles | 合计 |
|---|---:|---:|---:|---:|
| Prefill | 0 | 32,866 | 7,775 | 40,641 |
| Decode | 0 | 3,965 | 1,607 | 5,572 |

### 3.2 part2：Decoder 主体

part2 接收：

- part1 产生的 embedding；
- attention mask；
- 24 层的 past key/value cache。

part2 输出：

- logits；
- 24 层的新 key/value cache。

Prefill logits：

```text
UFIXED_POINT_16 [1,128,151936]
文件大小：38,895,616 bytes
```

Decode logits：

```text
UFIXED_POINT_16 [1,1,151936]
文件大小：303,872 bytes
```

### 3.3 Context binary 文件名与图编号反转

Profiling 命令显示：

- `part1` 加载 `..._2_of_2.serialized.bin`，内部执行图却是 `..._1_of_2`。
- `part2` 加载 `..._1_of_2.serialized.bin`，内部执行图却是 `..._2_of_2`。

当前输入输出匹配且执行成功，因此更像是模型打包时的文件命名顺序与内部图编号顺序不同。但部署脚本不能只根据文件名的 `1_of_2/2_of_2` 判断模型功能，应以 graph name 和 tensor I/O 为准。

## 4. Root 性能结果

### 4.1 分区明细

| 阶段 | 分区 | INIT | NETRUN EXECUTE | QNN EXECUTE | RPC EXECUTE | Accelerator EXECUTE | Accelerator excluding wait | Cycles | DE-INIT |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Prefill | part1 | 91.397 ms | 16.633 ms | 16.586 ms | 12.892 ms | 2.973 ms | 0.398 ms | 40,641 | 30.226 ms |
| Prefill | part2 | 284.610 ms | 1,018.468 ms | 1,015.991 ms | 933.571 ms | 96.060 ms | 93.279 ms | 153,878,355 | 195.163 ms |
| Decode | part1 | 96.192 ms | 16.519 ms | 16.479 ms | 12.845 ms | 2.880 ms | 0.330 ms | 5,572 | 32.146 ms |
| Decode | part2 | 289.640 ms | 699.364 ms | 699.205 ms | 663.712 ms | 30.206 ms | 27.527 ms | 33,729,756 | 157.445 ms |

### 4.2 两个 part 合计

| 阶段 | NETRUN EXECUTE 合计 | Accelerator 合计 | Excluding wait 合计 | Cycles 合计 | INIT 合计 | DE-INIT 合计 |
|---|---:|---:|---:|---:|---:|---:|
| Prefill 128 tokens | 1,035.101 ms | 99.033 ms | 93.677 ms | 153,918,996 | 376.007 ms | 225.389 ms |
| Decode 1 token | 715.883 ms | 33.086 ms | 27.857 ms | 33,735,328 | 385.832 ms | 189.591 ms |

在 detailed + optrace 下：

- Prefill 的 NETRUN execute 是 accelerator 时间的 10.45 倍。
- Decode 的 NETRUN execute 是 accelerator 时间的 21.64 倍。
- part2 的 RPC execute 分别达到 933.571 ms 和 663.712 ms。

这说明 Level 3 的 wall time 主要是详细 tracing 的采集和 RPC 同步成本，不是纯 HTP 运算时间。

若只用 accelerator execute 粗略观察 HTP 计算能力：

```text
Prefill：128 / 0.099033 ≈ 1292.5 tokens/s
Decode：1 / 0.033086 ≈ 30.22 tokens/s
```

这两个值仅用于定位硬件计算量，不代表 Genie App 的端到端吞吐。

## 5. part2 逐算子热点

### 5.1 Decode

| 算子类型 | 数量 | Cycles | 占 part2 比例 |
|---|---:|---:|---:|
| MatMul | 672 | 10,546,663 | 31.27% |
| Conv | 722 | 8,065,288 | 23.91% |
| Add | 768 | 6,266,684 | 18.58% |
| Softmax | 336 | 4,016,086 | 11.91% |
| Slice | 864 | 1,964,815 | 5.83% |
| Mul | 1,585 | 1,666,803 | 4.94% |
| Concat | 552 | 460,011 | 1.36% |
| RMSNorm | 49 | 385,400 | 1.14% |
| Output copy | 1 | 132,562 | 0.39% |

Decode 单算子热点：

| 排名 | 算子 | Cycles | 占比 |
|---:|---|---:|---:|
| 1 | `lm_head_conv_Conv` | 3,007,858 | 8.92% |
| 2 | `attn_0_head_0_qk_MatMul_0` | 171,051 | 0.51% |
| 3 | `attn_2_head_0_qk_MatMul_0` | 145,542 | 0.43% |
| 4 | `attn_7_head_0_qk_MatMul_0` | 139,363 | 0.41% |
| 5 | `attn_10_head_0_qk_MatMul_0` | 139,227 | 0.41% |

Decode 主要受 attention QK MatMul、投影 Conv、残差 Add、Softmax 和 LM Head 影响。由于 context length 固定为 4096，Decode 的 QK MatMul 需要持续访问大尺寸 KV cache，属于明显的带宽与矩阵运算热点。

### 5.2 Prefill

| 算子类型 | 数量 | Cycles | 占 part2 比例 |
|---|---:|---:|---:|
| Softmax | 336 | 52,826,717 | 34.33% |
| Add | 768 | 23,084,336 | 15.00% |
| Conv | 722 | 19,609,344 | 12.74% |
| Output copy | 1 | 17,925,947 | 11.65% |
| Mul | 1,585 | 17,067,956 | 11.09% |
| MatMul | 672 | 14,468,498 | 9.40% |
| RMSNorm | 49 | 4,771,721 | 3.10% |
| Slice | 864 | 2,761,027 | 1.79% |
| Concat | 552 | 462,120 | 0.30% |
| Transpose | 121 | 270,930 | 0.18% |

Prefill 单算子热点：

| 排名 | 算子 | Cycles | 占比 |
|---:|---|---:|---:|
| 1 | `Output OpId_3` | 17,925,947 | 11.65% |
| 2 | `lm_head_conv_Conv` | 9,000,143 | 5.85% |
| 3 | `layer_23_mlp_act_fn_Mul` | 427,106 | 0.28% |
| 4 | `layer_4_mlp_act_fn_Mul` | 419,831 | 0.27% |
| 5 | `layer_3_mlp_act_fn_Mul` | 418,439 | 0.27% |

336 个 Softmax 正好对应：

```text
24 Transformer layers × 14 attention heads = 336
```

这说明 attention 图被拆成逐 head 的 Softmax/MatMul，而没有形成更高粒度的融合 attention。它在 Prefill 中消耗 34.33% cycles，是最优先的图优化对象。

## 6. Level 3 与 Level 2 对比

### 6.1 分区对比

| 阶段 | 分区 | L2 NETRUN | L3 NETRUN | 变化 | L2 Accelerator | L3 Accelerator | 变化 | L2 cycles | L3 cycles | cycles 变化 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Prefill | part1 | 7.517 ms | 16.633 ms | +121.27% | 2.865 ms | 2.973 ms | +3.77% | 40,814 | 40,641 | -0.42% |
| Prefill | part2 | 984.033 ms | 1,018.468 ms | +3.50% | 95.967 ms | 96.060 ms | +0.10% | 153,286,674 | 153,878,355 | +0.39% |
| Decode | part1 | 8.580 ms | 16.519 ms | +92.53% | 2.779 ms | 2.880 ms | +3.63% | 5,012 | 5,572 | +11.17% |
| Decode | part2 | 679.314 ms | 699.364 ms | +2.95% | 30.756 ms | 30.206 ms | -1.79% | 34,193,595 | 33,729,756 | -1.36% |

part1 的 cycles 极小，因此 Decode part1 的 +11.17% 百分比变化只对应 560 cycles，不具有实际性能意义。

### 6.2 合计对比

| 阶段 | L2 NETRUN 合计 | L3 NETRUN 合计 | 变化 | L2 Accelerator 合计 | L3 Accelerator 合计 | 变化 |
|---|---:|---:|---:|---:|---:|---:|
| Prefill | 991.550 ms | 1,035.101 ms | +4.39% | 98.832 ms | 99.033 ms | +0.20% |
| Decode | 687.894 ms | 715.883 ms | +4.07% | 33.535 ms | 33.086 ms | -1.34% |

结论：

- optrace 使总 NETRUN wall time 增加约 4%。
- Accelerator 时间和 cycles 基本保持不变。
- Level 2 与 Level 3 对模型热点的判断一致。
- Level 3 更适合看逐算子轨迹和相对占比，不适合报告真实延迟。

## 7. 性能优化建议

### P0：Prefill 只计算并输出最后一个 token 的 logits

当前 Prefill 输出：

```text
[1,128,151936]
```

自回归生成通常只需要最后一个 token 的 logits。应尽量在 LM Head 之前执行 last-token slice：

```text
hidden_states [1,128,896]
    ↓ Slice last token
[1,1,896]
    ↓ LM Head
logits [1,1,151936]
```

预期收益：

- logits 输出从 38,895,616 bytes 降至 303,872 bytes，缩小 128 倍。
- 消除当前 11.65% 的大部分 Output Op 成本。
- LM Head 只处理一个位置，可进一步减少当前 5.85% 的 LM Head cycles。

### P0：检查 Attention 融合

当前每层、每个 head 都出现独立 Softmax 和 MatMul：

- 336 Softmax；
- 672 MatMul；
- 6,202 个 part2 子事件。

建议：

1. 检查模型导出是否破坏 QNN attention pattern matching。
2. 检查 reshape、transpose、slice、mask Add 是否阻断 attention fusion。
3. 优先采用 Qualcomm 已验证的 LLM/Genie 导出 recipe。
4. 如果资源确实由 QNN 2.29 生成，使用 QAIRT 2.34 重新转换和生成 context binary 做 A/B 测试。
5. 对比更新版本 QAIRT 的 HTP Transformer/attention 图优化能力。

### P1：降低固定 4096 context 对 Decode 的影响

当前图名为 `cl4096`，KV 输入按接近 4096 长度进行布局。Decode 的 attention MatMul、Slice、Add 和 cache 搬运因此较重。

可评估：

- 针对常用上下文长度生成 1024/2048/4096 多套图；
- 运行时根据真实上下文选择较小图；
- 检查是否能使用动态有效长度或有效 KV window；
- 对长对话使用 sliding-window/cache 策略（前提是模型及精度允许）。

### P1：减少 part1 的独立调用固定开销

part1 实际计算只有几千到几万 cycles，但 optrace 模式下独立调用耗时约 16.5 ms。正式 Genie 流程中应：

- 两个 context 常驻；
- 避免每 token 重新加载 context；
- part1 输出直接传给 part2，避免 `.raw` 文件落盘；
- 使用共享 buffer/memhandle，减少 host copy；
- 评估 embedding 是否可以与 decoder 图合并，但需同时考虑 context binary 大小和内存限制。

### P2：统一模型生成版本

资源文件名包含 `qnn229`，运行时和 profile viewer 是 QAIRT 2.34。虽然当前 context 能正常加载，但建议确认：

- context binary 的实际生成版本；
- backend/runtime 与 context 的正式兼容矩阵；
- 是否因旧版转换结果错过新版融合和调度优化。

## 8. 建议的正式测速方法

当前数据用于定位算子热点，不用于发布端到端性能。正式测速建议：

1. 关闭 detailed/optrace，或使用 basic profiling。
2. 在同一进程中一次加载 part1、part2 context。
3. Warm-up 5～10 次。
4. 连续执行至少 50 次 Decode，报告 P50/P90/P99。
5. 分别报告：
   - context 初始化耗时；
   - TTFT（Time To First Token）；
   - Prefill tokens/s；
   - Decode tokens/s；
   - 单 token P50/P90 延迟；
   - 峰值内存；
   - 持续运行温度和降频情况。
6. 使用真实 Genie App 测量 part1 → part2 → sampler 的完整链路。
7. 用相同 prompt、相同 context length、相同设备温度进行 A/B 对比。

## 9. 总体判断

Level 3 与 Level 2 给出了稳定且一致的硬件侧结果。模型当前不是受 embedding 计算限制，而是受 part2 的 attention 拆分、LM Head、大词表 logits 输出及长 KV cache 影响。

优化优先顺序建议：

```text
1. Prefill 在 LM Head 前只保留最后一个 token
2. 恢复/增强 Attention 融合
3. 为不同 context length 准备更合适的图
4. 使用常驻 context 和共享内存连接两个 part
5. 用无 optrace 的集成式 Genie benchmark 验证收益
```
