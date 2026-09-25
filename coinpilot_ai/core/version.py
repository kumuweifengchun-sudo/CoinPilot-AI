"""源码与打包程序均从项目元数据读取版本号。"""
from pathlib import Path
import re
import sys
import tomllib


_ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
VERSION = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", VERSION):
    raise ValueError("版本号必须包含三或四段数字")
_parts = tuple(int(part) for part in VERSION.split("."))
if any(part > 65535 for part in _parts):
    raise ValueError("Windows 版本号每段不能超过 65535")
WINDOWS_VERSION = _parts + (0,) * (4 - len(_parts))
