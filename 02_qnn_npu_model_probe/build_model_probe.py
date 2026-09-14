"""Build portable Android HTP model probes using only the Python standard library."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / 'Qnn_model_HTP_V68'
DEFAULT_SDK = Path(r'E:\QualComm\AIStack\QAIRT\2.46.0.260424')
HOST_REQUIRED = ['libQnnHtp.so', 'libQnnCpu.so', 'libQnnHtpPrepare.so', 'libQnnSystem.so']
HOST_OPTIONAL = ['libQnnModelDlc.so', 'libQnnSaver.so', 'libQnnHtpNetRunExtensions.so',
                 'libQnnHtpProfilingReader.so', 'libQnnHtpOptraceProfilingReader.so',
                 'libQnnChrometraceProfilingReader.so']


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def ask(label, default):
    value = input(f'{label} [{default}]: ').strip().strip('"')
    return value or str(default)


def architectures(sdk):
    return sorted(p.name.split('-v')[-1] for p in (sdk / 'lib').glob('hexagon-v[0-9][0-9]')
                  if (p / 'unsigned' / f'libQnnHtpV{p.name.split("-v")[-1]}Skel.so').is_file()
                  and (sdk / 'lib/aarch64-android' / f'libQnnHtpV{p.name.split("-v")[-1]}Stub.so').is_file())


def fresh_output(arch):
    base = ROOT / f'Qnn_model_HTP_V{arch}_{datetime.now():%Y%m%d_%H%M%S_%f}'
    result, suffix = base, 1
    while result.exists():
        result = base.with_name(f'{base.name}_{suffix}')
        suffix += 1
    return result


def plan(sdk, arch, output, assets):
    if not re.fullmatch(r'\d{2}', arch):
        raise ValueError('架构须为两位数字，例如 68、73、75。')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('输出目录必须不存在或为空，不覆盖已有检测包。')
    if output == TEMPLATE or output in TEMPLATE.parents or TEMPLATE in output.parents:
        raise ValueError('输出目录不能与模板目录重叠。')
    metadata = (sdk / 'sdk.yaml').read_text(encoding='utf-8-sig')
    version = re.search(r'^version:\s*([^\r\n]+)', metadata, re.M)
    build = re.search(r'^build_id:\s*([^\r\n]+)', metadata, re.M)
    if not version or not build:
        raise ValueError('sdk.yaml 缺少 version 或 build_id。')
    if arch not in architectures(sdk):
        raise ValueError(f'SDK 缺少 V{arch} Android Stub 或 DSP Skel；可用架构：{architectures(sdk)}')
    files = []

    def add(source, target):
        if not source.is_file():
            raise FileNotFoundError(f'缺少必要资源：{source}')
        files.append((source, target))

    for name in ['qnn-net-run', 'qnn-platform-validator']:
        add(sdk / 'bin/aarch64-android' / name, f'runtime/bin/{name}')
    for name in HOST_REQUIRED + [f'libQnnHtpV{arch}Stub.so']:
        add(sdk / 'lib/aarch64-android' / name, f'runtime/lib/{name}')
    for name in HOST_OPTIONAL:
        source = sdk / 'lib/aarch64-android' / name
        if source.is_file():
            add(source, f'runtime/lib/{name}')
    for source in sorted((sdk / f'lib/hexagon-v{arch}/unsigned').iterdir()):
        if source.is_file():
            add(source, f'runtime/dsp/{source.name}')
    assets_metadata = json.loads((assets / 'assets_manifest.json').read_text(encoding='utf-8'))
    if (assets_metadata['sdk_version'] != version.group(1).strip()
            or assets_metadata['sdk_build_id'] != build.group(1).strip()):
        raise ValueError('模型资源与运行库 SDK 版本/构建号不同，请先用目标 SDK 运行 build_model_assets.bat。')
    for folder in ['model', 'inputs']:
        for source in sorted((assets / folder).rglob('*')):
            if source.is_file() and '__pycache__' not in source.parts:
                add(source, source.relative_to(assets).as_posix())
    model = assets / 'model/libconv_relu_quantized.so'
    model_data = model.read_bytes()
    if (model_data[:4] != b'\x7fELF' or model_data[4] != 2
            or struct.unpack_from('<H', model_data, 18)[0] != 183
            or b'QnnModel_composeGraphs' not in model_data):
        raise ValueError('模型必须为提供 QnnModel_composeGraphs 的 Android arm64 模型库。')
    template_manifest = json.loads((TEMPLATE / 'bundle_manifest.json').read_text(encoding='utf-8'))
    protected = {entry['path']: entry for entry in template_manifest['files']}
    assets_protected = {entry['path']: entry for entry in assets_metadata['files']}
    for source, target in files:
        if not target.startswith('runtime/'):
            entry = assets_protected.get(target)
            if not entry or digest(source) != entry['sha256']:
                raise ValueError(f'模板文件与原清单不符：{target}')
    for name in ['inputs/input_list.txt',
                 'inputs/1x299x299x3_float_1.raw', 'inputs/1x299x299x3_float_2.raw']:
        if name not in {target for _, target in files}:
            raise FileNotFoundError(f'缺少模板文件：{name}')
    script = (TEMPLATE / 'run_probe.py').read_text(encoding='utf-8')
    if digest(TEMPLATE / 'run_probe.py') != protected['run_probe.py']['sha256']:
        raise ValueError('检测脚本模板与清单不符。')
    if "manifest['htp_architecture'] != 68" not in script:
        raise ValueError('不识别的检测脚本模板。')
    script = script.replace('V68', f'V{arch}').replace("manifest['htp_architecture'] != 68",
                                                            f"manifest['htp_architecture'] != {int(arch)}")
    compile(script, 'run_probe.py', 'exec')
    return files, script, version.group(1).strip(), build.group(1).strip()


def main():
    parser = argparse.ArgumentParser(description='生成不同架构的 Android HTP 模型检测包；不带参数时交互输入。')
    parser.add_argument('--sdk-root', help='QAIRT SDK 根目录')
    parser.add_argument('--arch', help='HTP 架构，例如 68、73、75')
    parser.add_argument('--output', help='新目录或空目录')
    parser.add_argument('--label', help='包标签')
    parser.add_argument('--assets', help='build_model_assets.py 生成的模型资源目录')
    parser.add_argument('--non-interactive', action='store_true', help='缺少参数使用默认值，不询问')
    parser.add_argument('--dry-run', action='store_true', help='检查资源及参数，不创建输出文件')
    args = parser.parse_args()
    initial_output = fresh_output(args.arch or '75')
    print('模型探针生成器：示例 / 默认参数')
    print(f'  SDK: {args.sdk_root or DEFAULT_SDK}\n  架构: {args.arch or "75"}\n'
          f'  输出: {args.output or initial_output}\n  标签: {args.label or "Qnn_model_HTP_V" + (args.arch or "75")}')
    print('每项直接按回车使用方括号默认值；可输入其他值。输出默认带时间戳，保留已有包。\n')
    sdk_text = args.sdk_root or (str(DEFAULT_SDK) if args.non_interactive else ask('SDK 根目录', DEFAULT_SDK))
    sdk = Path(sdk_text).expanduser().resolve()
    default_assets = ROOT / f'model_assets_{sdk.name}'
    candidates = [p for p in ROOT.glob(f'model_assets_{sdk.name}*')
                  if p.is_dir() and (p / 'assets_manifest.json').is_file()]
    if candidates:
        default_assets = max(candidates, key=lambda p: (p / 'assets_manifest.json').stat().st_mtime_ns)
    assets = Path(args.assets or (str(default_assets) if args.non_interactive else ask('模型资源目录', default_assets))).expanduser().resolve()
    print(f'当前 SDK 可选架构：{", ".join(architectures(sdk)) or "未找到，请检查 SDK 路径"}')
    arch = args.arch or ('75' if args.non_interactive else ask('HTP 架构', '75'))
    if not re.fullmatch(r'\d{2}', arch):
        raise ValueError('架构须为两位数字。')
    default_output = initial_output if arch == (args.arch or '75') else fresh_output(arch)
    output = Path(args.output or (str(default_output) if args.non_interactive else ask('输出目录', default_output))).expanduser().resolve()
    label = args.label or (f'Qnn_model_HTP_V{arch}' if args.non_interactive else ask('包标签', f'Qnn_model_HTP_V{arch}'))
    files, script, version, build_id = plan(sdk, arch, output, assets)
    print(f'\n目标 V{arch}；QAIRT {version} / {build_id}；标签 {label}\n输出：{output}')
    print(f'模型资源：{assets}；已核对模型与运行库 SDK 版本/构建号一致。')
    if args.dry_run:
        print(f'[PASS] 构建预检通过，{len(files)} 个待复制资源；未生成文件，未访问设备。')
        return 0
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for source, relative in files:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        hashed = digest(target)
        if hashed != digest(source):
            raise ValueError(f'复制校验失败：{relative}')
        records.append(dict(path=relative, size_bytes=target.stat().st_size, sha256=hashed, source=str(source)))
    batch = '@echo off\r\nsetlocal\r\nchcp 65001 >nul\r\npython -X utf8 -B "%~dp0run_probe.py" %*\r\nexit /b %ERRORLEVEL%\r\n'
    for name, content in [('run_probe.py', script), ('check_npu_model.bat', batch)]:
        path = output / name
        path.write_bytes(content.encode('utf-8'))
        records.append(dict(path=name, size_bytes=path.stat().st_size, sha256=digest(path), source='generated'))
    manifest = dict(htp_architecture=int(arch), target_label=label, platform='aarch64-android',
                    sdk_version=version, sdk_build_id=build_id, source_sdk_root=str(sdk),
                    model_sdk_version=version, model_sdk_build_id=build_id, model_assets=str(assets),
                    python_source='system_PATH', adb_source='system_PATH',
                    device_validation=f'NOT_VERIFIED_ON_V{arch}', files=records)
    (output / 'bundle_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    guide = f'''# {label} 使用说明

本包用于 Android arm64 / HTP V{arch}，运行资源来自 QAIRT {version}（{build_id}）。
模型由同版本 QAIRT {version} 的 Conv+ReLU 源码编译，在运行时构图，不是特定 HTP 架构的 context。
生成成功不等于设备支持或模型验证通过。

双击 check_npu_model.bat 开始检测。Windows 主机 PATH 中须有 adb.exe 和 Python 3.10 或更新版本，不附带 tools。
命令行：check_npu_model.bat -DeviceSerial YOUR_DEVICE_SERIAL -NoPause
本地校验：check_npu_model.bat --check-files（不访问设备）。

检测先校验文件，再清理固定设备目录 /data/local/tmp/qnn_npu_model_probe，重建并 push。
仅确认设备为 V{arch} 后，执行两组 HTP 推理及相同模型的 CPU 对照，检查元数据和输出差异。
每组 710432 个 float32 元素，绝对误差容差 0.109418；这是样例阈值，不是通用精度标准。
同一设备不要并行运行多个检测。日志保存在 probe_logs，设备文件下次运行时清理。
本包未经过 V{arch} 实机验证；PASS 仅适用于此模型、当前系统和 shell 环境。
'''
    (output / '使用说明.md').write_text(guide, encoding='utf-8')
    print('正在校验生成的完整检测包...', flush=True)
    subprocess.run([sys.executable, '-X', 'utf8', '-B', str(output / 'run_probe.py'), '--check-files'], check=True)
    print(f'[BUILT] {output}\n双击其中的 check_npu_model.bat 在对应架构设备上测试。')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f'[FAIL] {exc}')
        raise SystemExit(1)
