"""Select a model in Python and keep console errors visible."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser(description="选择模型并执行性能验证")
    parser.add_argument("--config", "-c", type=Path)
    parser.add_argument("--no-pause", action="store_true")
    args, extra = parser.parse_known_args()
    result = 1
    try:
        config = args.config
        if config is None:
            configs = sorted((ROOT / "models").glob("*/profile-config.json"))
            if not configs:
                raise RuntimeError("models 目录下没有 profile-config.json")
            print("可用模型：", flush=True)
            for i, item in enumerate(configs, 1):
                print(f"  {i}. {item.parent.name}", flush=True)
            choice = "1" if len(configs) == 1 else input(f"选择模型编号 [1-{len(configs)}]：").strip()
            if not choice.isdigit() or not 1 <= int(choice) <= len(configs):
                raise ValueError(f"无效模型编号：{choice!r}")
            config = configs[int(choice) - 1]
        config = config.resolve()
        if not config.is_file():
            raise FileNotFoundError(f"配置不存在：{config}")
        print(f"配置：{config}", flush=True)
        result = subprocess.run(
            [sys.executable, str(ROOT / "02_perf_validation.py"), "--config", str(config), *extra],
            cwd=ROOT,
        ).returncode
        if result:
            print(f"\n[失败] 性能验证退出码：{result}。请查看上方原始错误。", flush=True)
            print("若提示设备程序或输入不存在，请先运行 01_resource_push.cmd，选择同一模型部署资源。", flush=True)
        else:
            print("\n[完成] 性能采集结束；采集成功不代表性能达到业务指标。", flush=True)
    except (OSError, ValueError, RuntimeError, EOFError) as exc:
        print(f"\n[错误] {exc}", flush=True)
    except KeyboardInterrupt:
        result = 130
        print("\n已取消。", flush=True)
    finally:
        if not args.no_pause:
            try:
                input("\n按回车关闭窗口……")
            except (EOFError, KeyboardInterrupt):
                pass
    return result

if __name__ == "__main__":
    raise SystemExit(main())
