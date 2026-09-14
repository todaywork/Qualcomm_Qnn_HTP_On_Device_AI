import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from scripts.analyze_results import _extract_root_metrics, _extract_op_type
from scripts.report_zh import generate, level1_text_details, text_block


class ReportTests(unittest.TestCase):
    def test_actual_chat_input_and_multiline_output(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'genie-console.log').write_text(
                '[PROMPT]: <|im_start|>system\nyou are a assistant<|im_end|>\n'
                '<|im_start|>user\nhello<|im_end|>\n<|im_start|>assistant\n\n'
                '[BEGIN]: first\nsecond[END]\n', encoding='utf-8')
            self.assertEqual(level1_text_details(folder), ('you are a assistant', 'hello', 'first\nsecond'))
            (folder/'genie-console.log').write_text('[PROMPT]: plain\n[BEGIN]: answer[END]', encoding='utf-8')
            self.assertEqual(level1_text_details(folder), (None, 'plain', 'answer'))
        self.assertTrue(text_block('```').startswith('````text'))

    def test_accelerator_is_not_qnn_wrapper_and_deinit_is_separate(self):
        def row(message, value, event_id, source='BACKEND'):
            return dict(message=message, time=str(value), event_id=event_id,
                        timing_source=source, unit='US', event_level='ROOT')
        metrics = _extract_root_metrics({'rows': [
            row('INIT', 100, 'null', 'NETRUN'),
            row('DE-INIT', 20, 'null', 'NETRUN'),
            row('EXECUTE', 30, 'Accelerator (execute) time'),
            row('EXECUTE', 900, 'QNN accelerator (execute) time')]})
        self.assertEqual(metrics['accelerator_execute_us'], 30)
        self.assertEqual(metrics['init_us'], 100)
        self.assertEqual(metrics['deinit_us'], 20)
        self.assertNotIn('netrun_execute_us', metrics)

    def test_missing_data_and_chinese_structure(self):
        with TemporaryDirectory() as tmp:
            cfg = dict(output_dir=tmp, model_label='test', project='test', num_layers=1,
                       num_heads=1, hidden_size=1, kv_dim=1, vocab_size=100, context_length=512)
            report = generate(cfg, None, {}, {}, {})
        for title in ['1. 结论摘要', '2. 模型与图结构', '3. Level 1', '4. Level 2',
                      '5. Level 3', '6. 三级数据互证', '7. 优化建议', '8. 正式测速方法建议']:
            self.assertIn('## ' + title, report)
        self.assertIn('不可用', report)
        self.assertNotIn('0.000 ms', report)
        self.assertNotIn('0.000 tokens/s', report)

    def test_output_hotspot_category(self):
        self.assertEqual(_extract_op_type('Output'), 'Output')


if __name__ == '__main__':
    unittest.main()
