"""从独立工作目录启动源码与 EXE，验证资源路径和有请求时退出。"""

from pathlib import Path
import argparse
import os
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=ROOT / "dist" / "coinpilot-ai" / "coinpilot-ai.exe",
                        help="待验证的目录版或已安装 EXE")
    args = parser.parse_args(argv)
    executable = args.exe.resolve()
    if not executable.is_file():
        parser.error(f"找不到待验证程序：{executable}")
    commands = [
        ("源码悬浮窗", [sys.executable, str(ROOT / "coinpilot-ai.py")], []),
        ("源码设置页", [sys.executable, str(ROOT / "coinpilot-ai.py")], ["--settings"]),
        ("源码工作台", [sys.executable, str(ROOT / "coinpilot-ai.py")], ["--workbench"]),
        ("EXE 悬浮窗", [str(executable)], []),
        ("EXE 设置页", [str(executable)], ["--settings"]),
        ("EXE 工作台", [str(executable)], ["--workbench"]),
    ]
    with tempfile.TemporaryDirectory(prefix="coinpilot-ai-smoke-") as directory:
        env = dict(os.environ)
        for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM", "QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS"):
            env.pop(key, None)
        windows = Path(os.environ["SystemRoot"])
        env["PATH"] = os.pathsep.join((str(windows / "System32"), str(windows)))
        for title, command, extra in commands:
            process = subprocess.Popen(
                command + extra + ["--config", str(Path(directory) / "settings.json"),
                                    "--cache-dir", str(Path(directory) / "icons"), "--quit-after", "500"],
                cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                # 超时时清理本次测试的整个进程树，避免测试或子进程永久等待。
                subprocess.run([str(windows / "System32" / "taskkill.exe"), "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
                stdout, stderr = process.communicate(timeout=10)
                raise RuntimeError(f"{title}启动／退出超时：{stderr!r}") from None
            if process.returncode != 0 or b"Traceback" in stderr:
                log = Path(directory) / "settings.fault.log"
                details = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
                raise RuntimeError(f"{title}启动失败：{process.returncode} {stderr!r}\n{details}")
            print(f"{title}：启动及退出成功", flush=True)

        # 源码、EXE 及混合启动必须共用一个实例；次实例不能运行自己的 30 秒计时器。
        source = [sys.executable, str(ROOT / "coinpilot-ai.py")]
        frozen = [str(executable)]
        for index, (label, first, second) in enumerate((("源码", source, source), ("EXE", frozen, frozen), ("源码／EXE", source, frozen))):
            config = Path(directory) / f"duplicate-{index}.json"
            common = ["--config", str(config), "--cache-dir", str(Path(directory) / "icons")]
            primary = subprocess.Popen(first + common + ["--quit-after", "12000"], cwd=directory, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            duplicate = None
            try:
                deadline = time.monotonic()+8
                while not config.with_suffix(".workbench.sqlite3").exists():
                    if primary.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(label + "：主实例未就绪")
                    time.sleep(.05)
                duplicate = subprocess.Popen(second + common + ["--workbench", "--quit-after", "30000"],
                                             cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                _, errors = duplicate.communicate(timeout=8)
                assert duplicate.returncode == 0 and b"Traceback" not in errors, errors
                assert primary.poll() is None, "次实例不应关闭原有实例"
                _, errors = primary.communicate(timeout=16)
                assert primary.returncode == 0 and b"Traceback" not in errors, errors
                print(f"{label}：重复启动唤回并退出，主实例正常运行与关闭", flush=True)
            finally:
                for process in (duplicate, primary):
                    if process is not None and process.poll() is None:
                        subprocess.run([str(windows / "System32" / "taskkill.exe"), "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
                        process.communicate(timeout=10)


if __name__ == "__main__":
    main()
