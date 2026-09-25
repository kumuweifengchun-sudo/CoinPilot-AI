"""离线渲染更新界面；不检查发行源，不下载、不执行安装包。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT/"artifacts"/"updates")
    args = parser.parse_args()
    if args.all:
        for scale in ("1", "1.25", "1.5", "1.75", "2"):
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--output", str(args.output/scale)],
                           env=dict(os.environ, QT_SCALE_FACTOR=scale), check=True, timeout=30)
        return
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtGui import QFontDatabase
    from PyQt6.QtWidgets import QWidget
    from coinpilot_ai.app import create_application
    from coinpilot_ai.config import DEFAULT_CONFIG
    from coinpilot_ai.update_ui import UpdateController
    from coinpilot_ai.updater import Release

    class Owner(QWidget):
        configuration_changed = pyqtSignal(object)
        update_requested = pyqtSignal()

    app = create_application([])
    if sys.platform == "win32":
        for name in ("msyh.ttc", "msyhbd.ttc"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["SystemRoot"])/"Fonts"/name))
    owner = Owner()
    owner.config = dict(DEFAULT_CONFIG, proxy_enabled=False)
    controller = UpdateController(app, owner, args.output/"unused-cache", automatic=False)
    controller.install_supported = True  # 仅显示安装按钮；不点击、不启动助手。
    controller.client.release = Release("0.2.0", "example.exe", "", "", 40*1048576,
        "此为离线界面示例。\n\n- 改善 K 线缩放和拖动流畅度\n- 增加后台检查更新\n- 保留原有设置与交易记录")
    controller.client._state("available", "发现新版本 0.2.0。")
    controller.open()
    args.output.mkdir(parents=True, exist_ok=True)
    states = {"available": "发现新版本 0.2.0。", "downloading": "正在后台下载安装包，可继续使用程序。",
              "ready": "安装包已下载并通过 SHA-256 校验。", "error": "网络连接中断，请检查网络或代理后重试。"}
    try:
        for state, message in states.items():
            controller.client.received = 20*1048576
            controller.client._state(state, message)
            app.processEvents()
            dialog = controller.dialog
            assert dialog.width() <= 520
            assert dialog.action.isVisible()
            dialog.grab().save(str(args.output/(state+".png")))
        (args.output/"report.json").write_text(json.dumps({"scale": controller.dialog.devicePixelRatioF(),
            "states": list(states), "network_requests": 0, "installer_executed": False}, indent=2), encoding="utf-8")
    finally:
        controller.close()
        controller.deleteLater()
        owner.deleteLater()
        app.processEvents()
    print(str(args.output))


if __name__ == "__main__":
    main()
