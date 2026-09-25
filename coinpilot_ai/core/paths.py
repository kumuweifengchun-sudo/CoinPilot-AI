"""源码与 PyInstaller 程序共用的只读资源定位。"""
from pathlib import Path
import sys


def resource_path(name):
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / name
