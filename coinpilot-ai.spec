# 使用 uv run --group build pyinstaller --clean --noconfirm coinpilot-ai.spec
from pathlib import Path
from importlib.metadata import distribution
import os
import runpy
import sys

from PyQt6.QtCore import QLibraryInfo
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo,
)

root = Path(SPECPATH)
metadata = runpy.run_path(str(root / "coinpilot_ai" / "version.py"))
version, windows_version = metadata["VERSION"], metadata["WINDOWS_VERSION"]
file_version = ".".join(map(str, windows_version))
manifest = (root / "coinpilot-ai.manifest").read_text(encoding="utf-8").replace("@APP_VERSION@", file_version)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=windows_version, prodvers=windows_version),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "CoinPilot AI"),
            StringStruct("FileDescription", "CoinPilot AI · 币航 — AI 加密交易工作台"),
            StringStruct("FileVersion", file_version),
            StringStruct("InternalName", "coinpilot-ai"),
            StringStruct("OriginalFilename", "coinpilot-ai.exe"),
            StringStruct("ProductName", "CoinPilot AI"),
            StringStruct("ProductVersion", version),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)
datas = [
    (str(root / "pyproject.toml"), "."),
    (str(root / "coinpilot_ai" / "assets"), "coinpilot_ai/assets"),
    (str(root / "LICENSE"), "licenses/CoinPilotAI"),
    (str(Path(sys.base_prefix) / "LICENSE.txt"), "licenses/Python"),
]
# 直接收集锁定环境中的许可证，升级依赖时不会继续分发旧版本声明。
for package in ("PyQt6", "PyQt6-Qt6", "PyQt6-sip"):
    dist = distribution(package)
    licenses = [item for item in dist.files or [] if item.name.upper().startswith(("LICENSE", "COPYING"))]
    if not licenses:
        raise RuntimeError(f"缺少 {package} 的分发许可证")
    datas.extend((str(dist.locate_file(item)), f"licenses/{package}") for item in licenses)
# 隔离构建机的第三方 DLL 搜索路径，仅保留 Python、Qt 与 Windows。
# Qt 在 Windows 使用系统 TLS 后端，不依赖 PATH 中偶然存在的 OpenSSL。
windows = Path(os.environ["SystemRoot"])
os.environ["PATH"] = os.pathsep.join([
    str(Path(sys.executable).parent), sys.base_prefix,
    QLibraryInfo.path(QLibraryInfo.LibraryPath.BinariesPath),
    str(windows / "System32"), str(windows),
])
a = Analysis(
    [str(root / "coinpilot-ai.py")],
    pathex=[str(root)], binaries=[],
    datas=datas,
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
# Windows 10/11 自带 UCRT。避免从构建机 PATH 中混入第三方旧版
# ucrtbase.dll，否则 Qt 可能因缺失系统导出函数而无法启动。
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() != "ucrtbase.dll"
              and not Path(entry[0]).name.lower().startswith("api-ms-win-")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, contents_directory="_internal",
    name="coinpilot-ai", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    icon=str(root / "coinpilot_ai" / "assets" / "app_icon" / "app.ico"),
    manifest=manifest, version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="coinpilot-ai")
