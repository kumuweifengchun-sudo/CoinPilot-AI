"""Windows 启动内存基准：临时配置、阻断外网、独立进程，不接触用户数据。"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in
        ("peak_working_set", "working_set", "peak_paged", "paged", "peak_nonpaged",
         "nonpaged", "pagefile", "peak_pagefile", "private_bytes")]


def memory(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {key + "_mib": round(getattr(counters, key) / 2**20, 2)
                for key in ("working_set", "private_bytes")}
    finally:
        kernel.CloseHandle(handle)


def process_tree(pid):
    # Windows venv 的 python.exe 是启动器，实际 Qt 进程可能是其子进程。
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for name in ("Process32FirstW", "Process32NextW"):
        getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        valid = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while valid:
            parents[entry.pid] = entry.parent
            valid = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    result = {pid}
    while True:
        children = {child for child, parent in parents.items() if parent in result} - result
        if not children:
            return sorted(result)
        result.update(children)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--workbench", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32" or args.runs < 1:
        parser.error("需要 Windows，runs 必须大于 0")
    samples = []
    with tempfile.TemporaryDirectory(prefix="coinpilot-memory-") as folder:
        path = Path(folder)
        config = path / "settings.json"
        # 未监听的本机代理防止测试访问公开行情；新数据库没有账户凭据。
        config.write_text(json.dumps({"proxy_enabled": True, "proxy_type": "http",
                                      "proxy_host": "127.0.0.1", "proxy_port": 9}), encoding="utf-8")
        command = [str(args.exe.resolve())] if args.exe else [sys.executable, str(ROOT / "coinpilot-ai.py")]
        command += ["--config", str(config), "--cache-dir", str(path / "icons"), "--quit-after", "6000"]
        if args.workbench:
            command.append("--workbench")
        env = dict(os.environ, QT_QPA_PLATFORM="windows")
        for _ in range(args.runs):
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                time.sleep(4)
                if process.poll() is not None:
                    raise RuntimeError("基准进程提前退出")
                counters = [memory(pid) for pid in process_tree(process.pid)]
                sample = {key: round(sum(row[key] for row in counters), 2) for key in counters[0]}
                _, errors = process.communicate(timeout=12)
                if process.returncode or b"Traceback" in errors:
                    raise RuntimeError(f"基准失败：{process.returncode} {errors!r}")
                samples.append(sample)
            finally:
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    process.kill()
                    process.communicate()
    report = {"mode": "workbench" if args.workbench else "mini", "offline": True,
              "samples": samples, "median": {key: statistics.median(s[key] for s in samples) for key in samples[0]},
              "note": "工作集含共享页；private_bytes 为私有提交量，均不等同于任务管理器私有工作集。"}
    if args.exe:
        files = list(args.exe.resolve().parent.rglob("*"))
        report["distribution_mib"] = round(sum(p.stat().st_size for p in files if p.is_file()) / 2**20, 2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
