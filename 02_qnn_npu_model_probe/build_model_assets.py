"""Compile the SDK Conv+ReLU sample for Android arm64 and prepare matching inputs."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent
DEFAULT_SDK = Path(r'E:\QualComm\AIStack\QAIRT\2.46.0.260424')
DEFAULT_NDK = Path(r'D:\Sdk\ndk\27.2.12479018')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ask(label, default):
    return input(f'{label} [{default}]: ').strip().strip('"') or str(default)


def build(sdk, ndk, output, api=24):
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('输出目录须不存在或为空，不覆盖已有资源。')
    if not 21 <= api <= 35:
        raise ValueError('Android API 必须在 21 至 35 范围内，且所选 NDK 支持该 API。')
    sample = sdk / 'examples/QNN/converter/models'
    wrapper = sdk / 'share/QNN/converter/jni'
    toolbin = ndk / 'toolchains/llvm/prebuilt/windows-x86_64/bin'
    cpp = sample / 'qnn_model_8bit_quantized.cpp'
    archive = sample / 'qnn_model_8bit_quantized.bin'
    sources = [cpp, wrapper / 'QnnModel.cpp', wrapper / 'QnnWrapperUtils.cpp', wrapper / 'linux/QnnModelPal.cpp']
    inputs = [sample / f'input_data_float/1x299x299x3_float_{i}.raw' for i in (1, 2)]
    for path in sources + inputs + [archive, sdk / 'sdk.yaml', ndk / 'source.properties',
                                   toolbin / 'clang++.exe', toolbin / 'llvm-objcopy.exe', toolbin / 'llvm-readelf.exe']:
        if not path.is_file():
            raise FileNotFoundError(f'缺少构建依赖：{path}')
    cpp_text = cpp.read_text(encoding='utf-8')
    if 'convReluModel' not in cpp_text or '0.0547085' not in cpp_text:
        raise ValueError('SDK 样例与当前探针的模型/量化参数不匹配，不能直接套用比较阈值。')
    for path in inputs:
        if path.stat().st_size != 299 * 299 * 3 * 4:
            raise ValueError(f'输入尺寸不是 [1,299,299,3] float32：{path}')
    metadata = (sdk / 'sdk.yaml').read_text(encoding='utf-8-sig')
    version = re.search(r'^version:\s*([^\r\n]+)', metadata, re.M)
    build_id = re.search(r'^build_id:\s*([^\r\n]+)', metadata, re.M)
    if not version or not build_id:
        raise ValueError('SDK 元数据缺少版本或构建号。')
    # Read regular files only; never let an archive choose an extraction path.
    weights = {}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if not member.isfile() or not re.fullmatch(r'[A-Za-z0-9_]+\.raw', member.name):
                raise ValueError(f'不支持的权重条目：{member.name}')
            if member.name in weights or member.size > 16 * 1024 * 1024:
                raise ValueError('权重条目重复或超出此样例的预期大小。')
            weights[member.name] = tar.extractfile(member).read()
    referenced = set(re.findall(r'BINVARSTART\(\s*([A-Za-z0-9_]+)\s*\)', cpp_text))
    if {name + '.raw' for name in referenced} != set(weights):
        raise ValueError('模型源码引用的权重与 .bin 内容不一致。')
    work = output / 'build'
    (work / 'obj/binary').mkdir(parents=True)
    (output / 'model').mkdir()
    (output / 'inputs').mkdir()
    log = work / 'build.log'
    commands = []

    def run(command):
        commands.append([str(item) for item in command])
        result = subprocess.run(commands[-1], cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding='utf-8', errors='replace')
        with log.open('a', encoding='utf-8') as stream:
            stream.write(subprocess.list2cmdline(commands[-1]) + '\n' + result.stdout + '\n')
        if result.returncode:
            raise RuntimeError(f'编译命令失败，退出码 {result.returncode}，详见 {log}\n{result.stdout[-3000:]}')
        return result.stdout

    objects = []
    for name, data in weights.items():
        relative = 'obj/binary/' + name
        (work / relative).write_bytes(data)
        obj = 'obj/' + name + '.o'
        run([toolbin / 'llvm-objcopy.exe', '-I', 'binary', '-O', 'elf64-littleaarch64', '-B', 'aarch64', relative, obj])
        objects.append(obj)
    model = output / 'model/libconv_relu_quantized.so'
    print('正在使用 Android NDK 编译 arm64 模型库...', flush=True)
    run([toolbin / 'clang++.exe', f'--target=aarch64-linux-android{api}', '-shared', '-fPIC', '-O3',
         '-std=c++11', '-fvisibility=hidden', '-Wno-write-strings',
         '-DQNN_API=__attribute__((visibility("default")))', '-static-libstdc++',
         '-I' + str(wrapper), '-I' + str(sdk / 'include/QNN'), *sources, *objects,
         '-Wl,--no-undefined', '-Wl,-z,max-page-size=16384', '-Wl,-soname,libconv_relu_quantized.so',
         '-ldl', '-lm', '-o', model])
    data = model.read_bytes()
    if data[:4] != b'\x7fELF' or data[4] != 2 or struct.unpack_from('<H', data, 18)[0] != 183:
        raise ValueError('构建产物不是 Android arm64 ELF。')
    symbols = run([toolbin / 'llvm-readelf.exe', '--dyn-syms', '--wide', model])
    for symbol in ['QnnModel_composeGraphs', 'QnnModel_freeGraphsInfo']:
        if not any(symbol in line and 'GLOBAL' in line and 'UND' not in line for line in symbols.splitlines()):
            raise ValueError(f'模型未导出必要符号：{symbol}')
    for source in inputs:
        shutil.copy2(source, output / 'inputs' / source.name)
    (output / 'inputs/input_list.txt').write_text(''.join(f'inputs/{p.name}\n' for p in inputs), encoding='ascii')
    records = [dict(path=p.relative_to(output).as_posix(), size_bytes=p.stat().st_size, sha256=digest(p))
               for folder in ['model', 'inputs'] for p in sorted((output / folder).iterdir())]
    manifest = dict(sdk_directory=sdk.name, sdk_version=version.group(1).strip(), sdk_build_id=build_id.group(1).strip(),
                    source_sdk_root=str(sdk), ndk_root=str(ndk), android_api=api,
                    ndk_revision=(ndk / 'source.properties').read_text(encoding='utf-8'),
                    model_format='Android arm64 QNN runtime graph model, not HTP context',
                    input_shape=[1, 299, 299, 3], output_shape=[1, 149, 149, 32], output_scale=0.0547085,
                    device_validation='NOT_VERIFIED', files=records,
                    sources=[dict(path=str(p), sha256=digest(p)) for p in sources + inputs + [archive]],
                    commands=commands)
    (output / 'assets_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / '使用说明.md').write_text('模型由所选 SDK 源码和 NDK 实际编译。inputs 中两组 raw 来自同一 SDK 官方样例，input_list.txt 为重新生成的相对路径列表。\n'
                                     'model 和 inputs 可供 build_model_probe.py --assets 指定使用。build 目录保留构建日志及中间文件供复现，不会打进检测包。\n'
                                     '模型不绑定 HTP V68/V73/V75；对应架构由检测包的 Stub/DSP 决定。尚未实机验证。\n', encoding='utf-8')
    print(f'[PASS] 已编译模型并生成两组输入模板：{output}')
    return manifest


def main():
    parser = argparse.ArgumentParser(description='从 QAIRT 官方 Conv+ReLU 样例编译模型库，准备 raw 与 input_list.txt。')
    parser.add_argument('--sdk-root')
    parser.add_argument('--ndk-root')
    parser.add_argument('--output')
    parser.add_argument('--api', type=int, default=24)
    parser.add_argument('--non-interactive', action='store_true')
    args = parser.parse_args()
    print(f'示例/默认：SDK={DEFAULT_SDK}\nNDK={DEFAULT_NDK}\n每项留空按回车使用默认值；无需选择 HTP 架构。')
    sdk = Path(args.sdk_root or (str(DEFAULT_SDK) if args.non_interactive else ask('SDK 路径', DEFAULT_SDK))).resolve()
    ndk = Path(args.ndk_root or (str(DEFAULT_NDK) if args.non_interactive else ask('NDK 路径', DEFAULT_NDK))).resolve()
    default = ROOT / f'model_assets_{sdk.name}'
    if default.exists():
        default = ROOT / f'model_assets_{sdk.name}_{datetime.now():%Y%m%d_%H%M%S_%f}'
    output = Path(args.output or (str(default) if args.non_interactive else ask('输出目录', default))).resolve()
    build(sdk, ndk, output, args.api)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'[FAIL] {exc}')
        raise SystemExit(1)
