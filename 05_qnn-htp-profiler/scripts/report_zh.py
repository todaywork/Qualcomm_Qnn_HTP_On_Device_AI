"""Chinese three-level report based on measured data, not historical conclusions."""
import json
import re
from datetime import datetime
from pathlib import Path

KEYS = ['prefill/part1', 'prefill/part2', 'decode/part1', 'decode/part2']


def level1_text_details(directory):
    """Read the actual CLI input/output, never the possibly edited config."""
    log = directory / 'genie-console.log'
    if not log.is_file():
        return None, None, None
    text = log.read_text(encoding='utf-8', errors='replace')
    match = re.search(r'\[PROMPT\]:[ ]?(.*?)\r?\n\s*\[BEGIN\]:[ ]?(.*?)\[END\]', text, re.S)
    if not match:
        return None, None, None
    prompt, output = match.groups()
    roles = dict(re.findall(r'<\|im_start\|>(system|user)\r?\n(.*?)<\|im_end\|>', prompt, re.S))
    if roles:
        return roles.get('system'), roles.get('user'), output
    return None, prompt.rstrip('\r\n'), output


def text_block(text):
    """Use a fence longer than any backtick sequence in generated text."""
    fence = '`' * max(3, max((len(m.group()) + 1 for m in re.finditer(r'`+', text)), default=3))
    return f'{fence}text\n{text}\n{fence}'


def generate(cfg, level1, level2, level3, device):
    from .analyze_results import _parse_profile_csv, _extract_root_metrics, _extract_sub_events, _classify_operators
    def collect(data):
        result = {}
        for key in KEYS:
            entry = (data or {}).get(key, {})
            path = next((Path(entry[n]) for n in ['profile_csv', 'profile_from_optrace']
                         if entry.get(n) and Path(entry[n]).is_file()), None)
            parsed = _parse_profile_csv(path) if path else {}
            result[key] = {'parsed': parsed, 'metrics': _extract_root_metrics(parsed),
                           'events': _extract_sub_events(parsed), 'path': path}
        return result
    l2, l3 = collect(level2), collect(level3)
    def fmt(value, scale=1, suffix=''):
        return '不可用' if value is None else f'{value / scale:,.3f}{suffix}'
    def ms(m, key):
        return fmt(m.get(key), 1000, ' ms')
    def total(data, stage, metric):
        values = [data[f'{stage}/part{i}']['metrics'].get(metric) for i in (1, 2)]
        return sum(values) if all(v is not None for v in values) else None
    def rate(data, stage):
        value = total(data, stage, 'accelerator_execute_us')
        return (128 if stage == 'prefill' else 1) * 1e6 / value if value and value > 0 else None
    lines = []
    def p(text=''):
        lines.extend([text, ''])
    def table(headers, rows):
        lines.append('| ' + ' | '.join(headers) + ' |')
        lines.append('|' + '|'.join(['---'] * len(headers)) + '|')
        for row in rows:
            lines.append('| ' + ' | '.join(str(x).replace('|', '\\|').replace('\n', ' ') for x in row) + ' |')
        lines.append('')
    root = Path(cfg['output_dir'])
    runtime = sorted({e['parsed'].get('metadata', {}).get('Backend version', '') for e in l2.values()} - {''})
    p(f"# {cfg['model_label']} HTP 三级性能分析整合报告")
    p(f"- 分析日期：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
      f"- 项目：{cfg['project']}\n- 模型资源标识：`{cfg['model_label']}`\n"
      f"- 模型参数：{cfg['num_layers']} 层 / {cfg['num_heads']} heads / hidden {cfg['hidden_size']} / KV dim {cfg['kv_dim']} / 词表 {cfg['vocab_size']:,} / context {cfg['context_length']}\n"
      f"- QAIRT/QNN Runtime（CSV 记录）：{', '.join(runtime) or '不可用'}\n"
      f"- 测试设备：{device.get('brand', '')} {device.get('model', '不可用')}（{device.get('hardware', '不可用')}），{device.get('abi', '不可用')}\n"
      f"- 设备序列号：{cfg.get('serial') or '自动选择'}\n- 数据目录：`{root}`\n"
      '- Level 2 参数：`--profiling_level detailed --perf_profile burst --num_inferences 1`\n'
      '- Level 3 参数：在 Level 2 基础上增加 `--profiling_option optrace`\n'
      '- 报告结构：沿用 20260820-143407-three-level-performance-analysis.md；数据与结论重新计算。')
    dialogs = (level1 or {}).get('dialogs', [])
    query = dialogs[0]['events'].get('GenieDialog_query', {}) if dialogs else {}
    p('## 1. 结论摘要')
    p(f"1. Genie 端到端：TTFT {ms(query, 'ttft_us')}，Prefill {fmt(query.get('prefill_rate'), suffix=' tokens/s')}，Decode {fmt(query.get('decode_rate'), suffix=' tokens/s')}。\n"
      f"2. Level 2 两 part 加速器耗时合计：Prefill {fmt(total(l2, 'prefill', 'accelerator_execute_us'), 1000, ' ms')}，Decode {fmt(total(l2, 'decode', 'accelerator_execute_us'), 1000, ' ms/token')}。\n"
      f"3. Level 2 加速器时间估算：Prefill {fmt(rate(l2, 'prefill'), suffix=' tokens/s')}（128 token 整图），Decode {fmt(rate(l2, 'decode'), suffix=' tokens/s')}。\n"
      '4. Detailed/Optrace 用于定位热点；单次采集不能证明稳定性能，也不能替代真实 App 的测速。')
    missing = [f'{label} {key}' for label, data in [('L2', l2), ('L3', l3)] for key in KEYS
               if 'netrun_execute_us' not in data[key]['metrics']]
    p('数据完整性：' + ('以下项未取得 NETRUN EXECUTE 数据：' + '、'.join(missing) + '；不可当作零耗时。' if missing else 'L2/L3 四个组合均取得 NETRUN EXECUTE 记录。'))
    p('## 2. 模型与图结构')
    graph_rows = []
    for path in sorted((root / 'context_info' / 'context_info').glob('graph*.json')):
        try:
            content = json.loads(path.read_text(encoding='utf-8'))
            for graph in content.get('info', {}).get('graphs', []):
                info = graph.get('info', {})
                name = info.get('graphName', '')
                if f"cl{cfg['context_length']}_" not in name:
                    continue
                def tensors(field):
                    vals = [t.get('info', t) for t in info.get(field, [])]
                    return '; '.join(f"{v.get('name')} {v.get('dimensions')}" for v in vals[:3]) + (f'；共 {len(vals)} 项' if len(vals) > 3 else '')
                graph_rows.append([path.name, name, tensors('graphInputs'), tensors('graphOutputs')])
        except (OSError, ValueError):
            continue
    table(['元数据文件', '实际图名', '输入（节选）', '输出（节选）'], graph_rows or [['不可用'] * 4])
    p('真实生成由 Genie 连接模型分片、KV cache 与采样流程。Level 2/3 使用合成 raw 输入分别运行各图，不是完整自然语言生成。part 与 binary 的对应以 graph name、tensor I/O 和 part_mapping 为准，不能仅按文件后缀判断。')
    p('## 3. Level 1：Genie 端到端 Profiling')
    p('数据来源：`level1/genie-profile.json`。包含 Genie 调度和生成链路，仍需在实际 App 中复测。')
    for dialog in dialogs:
        events = dialog['events']; create = events.get('GenieDialog_create', {}); q = events.get('GenieDialog_query', {})
        prompt_tokens, prefill_rate = q.get('prompt_tokens'), q.get('prefill_rate')
        prefill_ms = prompt_tokens / prefill_rate * 1000 if prompt_tokens is not None and prefill_rate and prefill_rate > 0 else None
        p(f"### 会话：{dialog['name']}")
        table(['指标', '数值', '含义'], [
            ['初始化耗时', ms(create, 'init_time_us'), 'Genie 记录的初始化时间'],
            ['会话创建总耗时', ms(create, 'duration_us'), 'create 事件持续时间'],
            ['输入 token 数', fmt(q.get('prompt_tokens')), '本次输入长度'],
            ['首 token 延迟（TTFT）', ms(q, 'ttft_us'), 'Genie 记录的首 token 时间'],
            ['Prefill 速度', fmt(q.get('prefill_rate'), suffix=' tokens/s'), '输入处理吞吐'],
            ['生成 token 数', fmt(q.get('gen_tokens')), '本次输出长度'],
            ['Decode 速度', fmt(q.get('decode_rate'), suffix=' tokens/s'), '逐 token 生成吞吐'],
            ['整个输入语料 Prefill 耗时（反算）', fmt(prefill_ms, suffix=' ms'), '全部输入 token 数 ÷ Prefill tokens/s；包括实际输入中的系统提示词、用户提示词及模板 token'],
            ['本次输出 Decode 耗时', ms(q, 'decode_time_us'), 'Genie 原始 token-generation-time；按 Genie 生成阶段口径，不用输出总 token 数除速度代替'],
            ['整体推理耗时（不含初始化）', ms(q, 'duration_us'), 'GenieDialog_query 持续时间；本次完整查询耗时']])
        p('耗时口径：Prefill 耗时由吞吐反算，并非独立计时字段；Decode 耗时取原始生成计时。整体推理耗时取 query 原始记录，包含查询内其他处理开销，不强行用 Prefill + Decode 求和。整体耗时不含模型初始化、会话创建/释放、ADB 传输和 App 界面耗时。这里的“整个语料”指本次查询的完整输入，不是多条语料批次。')
    if not dialogs:
        p('不可用：未取得有效 Genie 会话数据。')
    p('### 本次推理输入与输出')
    p('提示词和生成文本取自本次 `level1/genie-console.log`，不使用可能已被修改的配置倒推历史输入。')
    system_prompt, user_prompt, generated_text = level1_text_details(root / 'level1')
    p('**推理前：系统提示词**')
    p(text_block(system_prompt) if system_prompt is not None else ('未设置独立 system 消息。' if user_prompt is not None else '不可用：日志中未找到完整输入输出记录。'))
    p('**推理前：用户提示词**')
    p(text_block(user_prompt) if user_prompt is not None else '不可用。')
    p('**推理后：文本结果**')
    p(text_block(generated_text) if generated_text is not None else '不可用：未找到完整的 [BEGIN]/[END] 输出段，请查看原始日志。')
    p('**推理后：输出 token 数量**')
    for dialog in dialogs:
        count = dialog['events'].get('GenieDialog_query', {}).get('gen_tokens')
        p(f"{dialog['name']}：{int(count) if count is not None else '不可用'} tokens（Genie 原始字段 `num-generated-tokens`；不是字符数或单词数）。")
    if not dialogs:
        p('不可用：未取得 Genie 输出 token 记录。')
    p('## 4. Level 2：QNN Detailed Profiling')
    p('### 4.1 性能汇总')
    table(['阶段', '分区', 'NETRUN EXECUTE', 'Accelerator EXECUTE', 'Accelerator cycles'], [
        [key.split('/')[0] + (' 128 tokens' if key.startswith('prefill') else ' 1 token'), key.split('/')[1],
         ms(l2[key]['metrics'], 'netrun_execute_us'), ms(l2[key]['metrics'], 'accelerator_execute_us'), fmt(l2[key]['metrics'].get('accelerator_cycles'))] for key in KEYS])
    p('### 4.2 为什么不能把 wall time 当推理性能')
    p('NETRUN EXECUTE 是执行调用的墙钟耗时；Accelerator EXECUTE 是后端报告的加速器耗时。两者差值可能包含 RPC、等待和 profiling 等开销，不能只凭差值确定原因。初始化与整个进程耗时也不能混为同一口径。')
    for stage, tokens in [('prefill', 128), ('decode', 1)]:
        p(f"{stage}：NETRUN 两 part 合计 {fmt(total(l2, stage, 'netrun_execute_us'), 1000, ' ms')}；加速器合计 {fmt(total(l2, stage, 'accelerator_execute_us'), 1000, ' ms')}。吞吐估算 = {tokens} / 加速器合计秒数 = {fmt(rate(l2, stage), suffix=' tokens/s')}。")
    p('128 是当前采集脚本的 Prefill AR，不能用 context length 替代。分片独立运行时间的求和仅为诊断估算，不代表 Genie 实测速度，也不是 TOPS。')
    for number, stage in [(3, 'prefill'), (4, 'decode')]:
        p(f'### 4.{number} part2 热点：{stage.capitalize()}')
        events = l2[f'{stage}/part2']['events']; denominator = sum(e['cycles'] for e in events)
        if not denominator:
            p('不可用：没有有效逐算子 cycles，无法计算热点或占比。'); continue
        groups = sorted(_classify_operators(events).items(), key=lambda item: item[1]['cycles'], reverse=True)
        table(['算子类型', '数量', 'Cycles', '占比'], [[name, v['count'], f"{v['cycles']:,}", f"{v['cycles']/denominator:.2%}"] for name, v in groups[:10]])
        p('单算子 Top 5：')
        table(['排名', '算子', 'Cycles', '占比'], [[i, f"`{v['name']}`", f"{v['cycles']:,}", f"{v['cycles']/denominator:.2%}"] for i, v in enumerate(sorted(events, key=lambda e: e['cycles'], reverse=True)[:5], 1)])
        p(f'本阶段观测到的首要算子类别是 {groups[0][0]}。占比分母为已采集 SUB-EVENT cycles 之和；算子可能融合或重叠，不能直接解释为端到端耗时占比。是否未融合、是否存在无效搬运，需要结合图结构验证。')
    p('## 5. Level 3：Optrace Profiling')
    p('### 5.1 与 Level 2 对比')
    table(['阶段', '分区', 'L2 NETRUN', 'L3 NETRUN', 'L2 Accel', 'L3 Accel', 'L2 cycles', 'L3 cycles'], [
        key.split('/') + [ms(d[key]['metrics'], m) for m in ['netrun_execute_us', 'accelerator_execute_us'] for d in [l2, l3]] + [fmt(d[key]['metrics'].get('accelerator_cycles')) for d in [l2, l3]] for key in KEYS])
    for stage in ['prefill', 'decode']:
        a, b = total(l2, stage, 'accelerator_execute_us'), total(l3, stage, 'accelerator_execute_us')
        p(f'{stage} 加速器合计 L3 相对 L2 变化：' + (f'{(b/a-1)*100:+.2f}%。' if a and b is not None else '不可用。'))
    p('Optrace 的价值是逐算子轨迹与时序分析。不能预设它与 Detailed 完全一致，也不能用单次结果认定硬件计算量稳定。')
    p('## 6. 三级数据互证')
    table(['维度', 'Level 1（端到端）', 'Level 2 加速器估算', 'Level 3 加速器估算'], [
        [stage, fmt(query.get(field), suffix=' tokens/s'), fmt(rate(l2, stage), suffix=' tokens/s'), fmt(rate(l3, stage), suffix=' tokens/s')]
        for stage, field in [('decode', 'decode_rate'), ('prefill', 'prefill_rate')]])
    p(f"Level 1 输入 {fmt(query.get('prompt_tokens'))} tokens，Level 2/3 Prefill 固定 128 tokens；输入和采集开销不同，不宜直接比较。Decode 也需核对 KV 长度、频率和运行条件，不能仅因数值接近就认定上层开销很小。")
    p('## 7. 优化建议（按优先级）')
    for title, content in [
        ('P0：先保证采集数据完整', '检查原始 EXECUTE 事件、Reader 与运行库版本。缺失指标显示不可用；不根据空数据给优化收益。'),
        ('P0：检查 Prefill 输出与 Attention 热点', '若元数据确认输出全序列 logits 且业务只需最后位置，可评估 LM Head 前截取最后 token。若 Softmax/MatMul 占比高，结合图结构检查 Attention 融合；仅凭算子数量不能证明未融合。'),
        ('P1：评估 context 长度与 KV cache', f"当前 context 为 {cfg['context_length']}；按业务长度评估多套图，记录精度、速度和内存的变化。"),
        ('P1：减少分片调用与拷贝', '在真实 Genie 流程中验证 context 常驻、共享 buffer 和重复加载情况，再做 A/B 测试。'),
        ('P2：核对模型生成与运行库版本', '采用受支持的版本组合，重新生成模型后验证兼容性、精度和性能；不直接套用历史环境的结论。')]:
        p('### ' + title); p(content)
    p('## 8. 正式测速方法建议')
    p('1. 使用低开销 profiling 建立速度基准，Detailed/Optrace 用于诊断。\n2. 同一进程常驻加载模型分片。\n3. 建议预热 5–10 次，至少采集 50 个可比样本，报告 P50/P90/P99 与失败率。\n4. 分别记录初始化、TTFT、Prefill/Decode 速度、峰值内存及持续温度。\n5. 使用真实 App 完成输入、生成、取消和生命周期测试。\n6. A/B 保持模型、prompt、context、采样配置和设备条件一致。\n7. 当前脚本未持续采集内存、温度、NPU 利用率或进程级 TOPS，这些指标需另行补齐。')
    p('## 附录：数据文件位置')
    table(['来源', '路径', '修改时间'], [[label, str(path), datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds') if path.is_file() else '不可用']
        for label, path in [('Level 1', root/'level1'/'genie-profile.json')] + [(f'{label} {key}', entry['path']) for label, data in [('Level 2', l2), ('Level 3', l3)] for key, entry in data.items() if entry['path']]])
    p('复现命令（工具根目录执行）：\n\n```powershell\npython 02_perf_validation.py --config "' + str(root/'profile-config.json') + '"\n```')
    return '\n'.join(lines)
