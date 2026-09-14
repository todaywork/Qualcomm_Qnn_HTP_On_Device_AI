"""Portable V73 model probe; Python standard library only."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import shutil
from datetime import datetime

ROOT = Path(__file__).resolve().parent
REMOTE = '/data/local/tmp/qnn_npu_model_probe'
COUNT = 149 * 149 * 32
TOLERANCE = 2 * 0.0547085 + 0.000001


def verify_bundle():
    manifest = json.loads((ROOT / 'bundle_manifest.json').read_text(encoding='utf-8'))
    if manifest['htp_architecture'] != 73:
        raise ValueError('清单架构不是 V73。')
    listed = set()
    for item in manifest['files']:
        path = (ROOT / item['path']).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('清单路径超出检测包目录。')
        if path.stat().st_size != item['size_bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError(f'文件校验失败：{item["path"]}')
        listed.add(item['path'])
    for folder in ['runtime', 'model', 'inputs']:
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and path.relative_to(ROOT).as_posix() not in listed:
                raise ValueError(f'存在未登记文件：{path}')
    print(f'[PASS] V73 本地资源 SHA-256 校验通过：{len(listed)} 个文件；不代表实机验证通过。')


def compare(hpath, cpath):
    h, c = hpath.read_bytes(), cpath.read_bytes()
    if len(h) != COUNT * 4 or len(c) != COUNT * 4:
        raise ValueError('输出张量尺寸错误，预期每组 710432 个 float32 元素。')
    maximum = total = 0.0
    mismatches = 0
    for (a,), (b,) in zip(struct.iter_unpack('<f', h), struct.iter_unpack('<f', c)):
        if not math.isfinite(a) or not math.isfinite(b):
            raise ValueError('输出存在 NaN 或无穷值。')
        error = abs(a - b)
        maximum = max(maximum, error)
        total += error
        mismatches += error > TOLERANCE
    return dict(Elements=COUNT, MaxAbsError=maximum, MeanAbsError=total / COUNT,
                Tolerance=TOLERANCE, MismatchedElements=mismatches, Passed=mismatches == 0)


def main():
    parser = argparse.ArgumentParser(description='V73 HTP 模型推理及 CPU 输出对比')
    parser.add_argument('-DeviceSerial', '--device-serial', dest='device_serial', default='')
    parser.add_argument('-NoPause', '--no-pause', dest='no_pause', action='store_true')
    parser.add_argument('--check-files', action='store_true', help='仅校验本地文件，不连接设备')
    args = parser.parse_args()
    if args.check_files:
        try:
            verify_bundle()
            return 0
        except Exception as exc:
            print(f'[FAIL] {exc}')
            return 1
    logs = ROOT / 'probe_logs' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    serial = args.device_serial
    selection = ['-s', serial] if serial else []
    code = 1

    def save(name, data):
        (logs / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def adb(name, *command, show=False):
        result = subprocess.run([adb_path, *selection, *command],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding='utf-8', errors='replace')
        with (logs / name).open('a', encoding='utf-8') as stream:
            stream.write(result.stdout)
        if show:
            print(result.stdout.rstrip())
        if result.returncode:
            raise RuntimeError(f'ADB 执行失败，退出码 {result.returncode}，请查看 {name}')
        return result.stdout

    try:
        logs.mkdir(parents=True)
        verify_bundle()
        adb_path = shutil.which('adb.exe')
        if not adb_path:
            raise FileNotFoundError('系统 PATH 中未找到 adb.exe，请配置 Android platform-tools。')
        print(f'使用系统 ADB：{adb_path}')
        required = ['runtime/bin/qnn-platform-validator',
                    'runtime/bin/qnn-net-run', 'model/libconv_relu_quantized.so',
                    'runtime/dsp/libQnnHtpV73Skel.so', 'inputs/input_list.txt',
                    'inputs/1x299x299x3_float_1.raw', 'inputs/1x299x299x3_float_2.raw']
        required += ['runtime/lib/' + name for name in
                     ['libQnnHtp.so', 'libQnnCpu.so', 'libQnnHtpPrepare.so', 'libQnnHtpV73Stub.so']]
        for name in required:
            if not (ROOT / name).is_file():
                raise FileNotFoundError(f'缺少随包文件：{name}')
        print('[1/6] 检查设备连接（本包适用 HTP V73）...')
        adb('devices.txt', 'devices', '-l')
        adb('connection.txt', 'get-state')
        serial = adb('serial.txt', 'get-serialno').strip()
        if not serial or serial == 'unknown':
            raise RuntimeError('无法确定设备序列号。')
        selection = ['-s', serial]
        info = adb('device_info.txt', 'shell',
                   'getprop ro.product.model; getprop ro.soc.model; getprop ro.product.cpu.abi; id', show=True)
        if 'arm64-v8a' not in info:
            raise RuntimeError('需要 arm64 Android 设备。')
        print('[2/6] 清空固定设备目录并部署资源...')
        if REMOTE != '/data/local/tmp/qnn_npu_model_probe':
            raise RuntimeError('拒绝清理非预期路径。')
        cleanup = ('test ! -L /data/local/tmp/qnn_npu_model_probe && '
                   'test "$(readlink -f /data/local/tmp)" = /data/local/tmp && '
                   'rm -rf /data/local/tmp/qnn_npu_model_probe && '
                   'mkdir -p /data/local/tmp/qnn_npu_model_probe')
        adb('deploy.txt', 'shell', cleanup)
        for folder in ['runtime/bin', 'runtime/lib', 'runtime/dsp', 'model', 'inputs']:
            adb('deploy.txt', 'push', str(ROOT / folder), REMOTE + '/')
        adb('deploy.txt', 'shell', f'chmod 755 {REMOTE}/bin/qnn-net-run')
        environment = (f'cd {REMOTE} && export LD_LIBRARY_PATH={REMOTE}/lib && '
                       f"export ADSP_LIBRARY_PATH='{REMOTE}/dsp;/vendor/lib/rfsa/adsp;/vendor/dsp;/dsp'")
        adb('deploy.txt', 'shell', f'chmod 755 {REMOTE}/bin/qnn-platform-validator')
        architecture = adb('core_version.txt', 'shell',
                           f'{environment} && ./bin/qnn-platform-validator --backend dsp --coreVersion', show=True)
        if not re.search(r'Hexagon Architecture V73\b', architecture):
            raise RuntimeError('设备架构不是 V73 或未能确认；停止模型推理。')
        options = '--model ./model/libconv_relu_quantized.so --input_list inputs/input_list.txt --log_level verbose'
        for stage, kind, backend in [(3, 'htp', 'libQnnHtp.so'), (4, 'cpu', 'libQnnCpu.so')]:
            print(f'[{stage}/6] 在 {"NPU/HTP" if kind == "htp" else "CPU（对照）"} 上执行两组输入...')
            adb(kind + '.txt', 'shell',
                f'{environment} && ./bin/qnn-net-run --backend ./lib/{backend} {options} --output_dir output_{kind}', show=True)
        print('[5/6] 下载本次输出并检查推理完成记录...')
        for kind, backend in [('htp', 'libQnnHtp.so'), ('cpu', 'libQnnCpu.so')]:
            name = 'output_' + kind
            adb('pull.txt', 'pull', f'{REMOTE}/{name}', str(logs / name))
            metadata = (logs / name / 'execution_metadata.yaml').read_text(encoding='utf-8')
            if not (re.search(r'inferences_completed:\s*2\b', metadata)
                    and re.search(r'graph_name:\s*convReluModel\b', metadata) and backend in metadata):
                raise RuntimeError(f'{name} 的后端、模型或完成次数不符合预期。')
        print('[6/6] 比较同一输入的 CPU 与 NPU 输出...')
        print('\n字段说明：')
        print('  Sample 0 / 1：第 1 / 2 组测试输入；每组都分别在 CPU 和 NPU 上执行。')
        print('  max error：该组所有输出元素中，|NPU 输出 - CPU 输出| 的最大值。')
        print(f'  tolerance：允许的绝对误差上限 {TOLERANCE:.6f}（2 个输出量化步长加浮点余量）。')
        print(f'  mismatches：误差超过上限的元素数量；每组共 {COUNT} 个元素。')
        print('  mismatches=0：所有元素均在容差内，不表示 CPU 与 NPU 输出完全相同。')
        print('  阈值解释：模型输出量化步长为 0.0547085。')
        print('    允许两个步长差异 + 浮点余量：')
        print('    2 × 0.0547085 + 0.000001 = 0.109418。')
        print('    两个步长是本样例选定的测试容差，不是 SDK 官方标准或理论误差上界。')
        print('    用于容纳 CPU/HTP 量化计算与舍入差异。\n')
        results = []
        for index in range(2):
            h = list((logs / 'output_htp' / f'Result_{index}').glob('*.raw'))
            c = list((logs / 'output_cpu' / f'Result_{index}').glob('*.raw'))
            if len(h) != 1 or len(c) != 1 or h[0].name != c[0].name:
                raise RuntimeError('预期每组有且只有一个同名输出张量。')
            result = compare(h[0], c[0])
            results.append(dict(Sample=index, Comparison=result))
            print_comparison(index, result)
        passed = all(r['Comparison']['Passed'] for r in results)
        save('comparison.json', dict(Passed=passed, Reference='Same quantized model on QNN CPU', Samples=results))
        if not passed:
            raise RuntimeError('输出对比超出容差。')
        code = 0
        print('\n[PASS] HTP 模型推理成功，两组输出均通过 CPU 对照校验。')
        print('结论仅适用于当前 shell 环境下这个 Conv+ReLU 模型，不代表所有模型或 APK 均可用。')
        print('原始输出：output_cpu/Result_0、Result_1 是 CPU；output_htp/Result_0、Result_1 是 NPU。')
    except Exception as exc:
        print(f'[FAIL] {exc}')
        if logs.exists():
            (logs / 'error.txt').write_text(str(exc), encoding='utf-8')
    finally:
        if logs.exists():
            save('result.json', dict(Passed=code == 0, ExitCode=code, Device=serial, Remote=REMOTE, ExpectedArchitecture='V73'))
        print(f'日志与原始输出目录：{logs}')
        print(f'设备目录：{REMOTE}（下次运行清空）')
        if not args.no_pause:
            try:
                input('按回车关闭窗口...')
            except EOFError:
                pass
    return code


def print_comparison(index, result):
    """Print the compact result; explanations appear once above all samples."""
    print(f'Sample {index}（第 {index+1} 组输入，CPU 对比 NPU）: '
          f'max error={result["MaxAbsError"]:.8f}, '
          f'tolerance={TOLERANCE:.6f}, '
          f'mismatches={result["MismatchedElements"]}/{result["Elements"]}')


if __name__ == '__main__':
    raise SystemExit(main())
