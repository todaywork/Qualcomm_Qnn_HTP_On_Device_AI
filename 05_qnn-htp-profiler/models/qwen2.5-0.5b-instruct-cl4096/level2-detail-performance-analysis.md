结论：这里分成两个 `part`，不是日志被拆成两段，而是 QNN 模型本身被拆成了两个图。

| 分区  | 功能                        | 输入                                | 输出                          |
| ----- | --------------------------- | ----------------------------------- | ----------------------------- |
| part1 | Token Embedding             | `input_ids`                         | `[batch, seq, 896]` embedding |
| part2 | 24 层 Transformer + LM Head | embedding、attention mask、KV cache | logits、新 KV cache           |

同时，每个 part 内又包含两种图：

- `ar128`：Prefill，一次处理 128 个 token
- `ar1`：Decode，一次生成 1 个 token

因此结构实际是：

```
Prefill: input_ids[1,128]
            ↓ part1：Embedding
        embedding[1,128,896]
            ↓ part2：24层 Decoder + LM Head
        logits + KV cache

Decode: input_ids[1,1]
            ↓ part1：Embedding
        embedding[1,1,896]
            ↓ part2：24层 Decoder + LM Head
        logits + KV cache
```

真实链路中：part2的embedding实际来自part1的输出。

embedding 就是 token id 的“向量化表示”，part1 负责把 input_ids 转成 [batch, seq, 896]，part2 再拿这个向量去做 Transformer 推理。

## 性能汇总

`profile.csv` 中的主要数据如下：

| 阶段               | 分区  | qnn-net-run EXECUTE | Accelerator EXECUTE | 加速器 cycles |
| ------------------ | ----- | ------------------- | ------------------- | ------------- |
| Prefill 128 tokens | part1 | 7.517 ms            | 2.865 ms            | 40,814        |
| Prefill 128 tokens | part2 | 984.033 ms          | 95.967 ms           | 153,286,674   |
| Decode 1 token     | part1 | 8.580 ms            | 2.779 ms            | 5,012         |
| Decode 1 token     | part2 | 679.314 ms          | 30.756 ms           | 34,193,595    |

part1 的计算量非常小：

- Prefill：约占总 accelerator cycles 的 0.027%
- Decode：约占总 accelerator cycles 的 0.015%

所以性能几乎完全由 part2 决定。part1 虽然只有一个 Gather，但独立 QNN 调用、RPC、功耗唤醒和数据搬运会产生毫秒级固定开销。

## 为什么 wall time 看起来特别慢

不能把下面两个数直接当作正常推理性能：

```
Prefill：7.517 + 984.033 ≈ 991.6 ms
Decode： 8.580 + 679.314 ≈ 687.9 ms/token
```

因为当前命令使用了：

```
--profiling_level detailed
--num_inferences 1
```

part2 记录了 6202 个逐算子事件。详细 profiling 带来的同步、RPC 和数据收集开销非常明显：

- Decode part2：QNN execute 679 ms，但 accelerator execute 只有 30.8 ms
- Prefill part2：QNN execute 984 ms，但 accelerator execute 只有 96.0 ms

以 accelerator execute 粗略估计、不考虑 Genie 上层开销：

- Prefill：约 `128 / 0.0988 ≈ 1295 token/s`
- Decode：约 `1 / 0.0335 ≈ 29.8 token/s`

这只能看成 HTP 计算性能的参考，不是最终 App 的端到端速度。

正式测速应该：

1. 关闭 detailed profiling，或改成 basic。
2. 保持两个 context 常驻，不要每次加载/卸载。
3. 先 warm-up 5～10 次。
4. 连续执行至少 50 次。
5. 在 Genie App 内测完整的 part1 → part2 数据链。

## part2 的热点

### Decode 热点

| 算子类型      | cycles 占比 |
| ------------- | ----------- |
| MatMul        | 31.05%      |
| Conv          | 24.17%      |
| Add           | 18.39%      |
| Softmax       | 11.86%      |
| Slice/KV 处理 | 5.97%       |
| Mul           | 5.07%       |

其中单独的：

```
lm_head_conv_Conv
```

占 Decode 总 cycles 的约 8.96%。

Decode 主要受以下因素限制：

- 每层 attention 的 QK MatMul
- LM Head 对 151,936 词表的投影
- 固定 4096 context 下的 KV cache 读写
- 大量逐 head 的算子调度

### Prefill 热点

| 算子类型 | cycles 占比 |
| -------- | ----------- |
| Softmax  | 34.38%      |
| Add      | 14.97%      |
| Conv     | 12.81%      |
| 输出搬运 | 11.60%      |
| Mul      | 11.06%      |
| MatMul   | 9.41%       |
| RMSNorm  | 3.11%       |

这里有两个非常明显的问题。

第一，模型有 336 个 Softmax：

```
24 layers × 14 attention heads = 336
```

说明 attention 很可能被拆成了逐 head 的 Softmax/MatMul，而没有形成更高效的融合 attention。Prefill 中 Softmax 独占 34.38%，是当前最大热点。

第二，Prefill 输出了全部 128 个位置的 logits：

```
[1, 128, 151936], UFIXED_POINT_16
```

输出文件大小为：

```
38,895,616 bytes ≈ 37.1 MiB
```

Decode logits 只有：

```
[1, 1, 151936]
303,872 bytes
```

Prefill 的 `Output Op` 因此占了 11.6% cycles。在常规自回归生成中，Prefill 通常只需要最后一个 token 的 logits。更好的图结构是：

```
hidden_states[1,128,896]
        ↓ 取最后一个 token
last_hidden[1,1,896]
        ↓ LM Head
logits[1,1,151936]
```

也就是在 LM Head 之前取最后一个 token。这样不仅输出缩小 128 倍，LM Head 也只计算一个位置，理论上还能减少当前 LM Head 的 5.96% Prefill cycles。

## 一个需要注意的命名反转

日志中存在容易混淆的地方：

- `part1` 实际加载的是文件名 `..._2_of_2.serialized.bin`
- 但文件内部图名是 `ar128_cl4096_1_of_2` / `ar1_cl4096_1_of_2`
- `part2` 加载的是 `..._1_of_2.serialized.bin`
- 文件内部图名却是 `..._2_of_2`

也就是说，context binary 文件后缀和内部 graph 的 part 编号是反的。

当前能正常运行，说明脚本是按实际 graph/input 对应的；但建议检查生成模型的配置文件，避免以后根据文件名误认为：

```
1_of_2.serialized.bin = part1
```

实际这批文件并不是这样。

另外，文件名显示模型实际是：

```
qwen2.5-0.5b-instruct
qcs8550
context length 4096
```

虽然工程目录叫 `Qwen3GenieDemo`，这份性能数据对应的是 Qwen2.5 0.5B，不是 Qwen3。文件名还标记了 `qnn229`，而 profiling runtime 是 QAIRT 2.34；如果该 context 确实由 QNN 2.29 生成，建议用 2.34 重新生成一次进行 A/B 测试，以确认能否获得更新版本的图优化。