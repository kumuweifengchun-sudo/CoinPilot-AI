"""应用入口。"""

import argparse
from pathlib import Path
import sys
import sqlite3

from PyQt6.QtCore import QCoreApplication, QEvent, QLibraryInfo, QLocale, QTimer, QTranslator, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from .config import ICON_CACHE_DIR, SETTINGS_FILE, SettingsStore
from .network import MarketClient
from .hotkey import GlobalHotkey
from .startup import StartupManager
from .visuals import font, load_fonts, resource_path
from .widget import CoinPilotWidget
from .theme import activate_theme, palette, style_sheet

_diagnostic_file = None


def create_application(argv):
    # Qt 6 已启用高 DPI；保留 125%/150% 等真实比例，字号不再手动乘 DPI。
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName("CoinPilot AI")
    app.setApplicationDisplayName("CoinPilot AI · 币航")
    app.setOrganizationName("CoinPilotAI")
    app.setStyle("Fusion")
    app.inter_font_loaded = load_fonts()
    app.setPalette(palette())
    app.setStyleSheet(style_sheet())
    app.setFont(font(13))
    app.setWindowIcon(QIcon(str(resource_path("coinpilot-ai.ico"))))
    QLocale.setDefault(QLocale("zh_CN"))
    translator = QTranslator(app)
    if translator.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    app.translator = translator
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="CoinPilot AI · 币航 — AI 加密交易工作台", add_help=False)
    parser.add_argument("-h", "--help", action="help", help="显示帮助信息")
    parser.add_argument("--settings", action="store_true", help="启动后打开设置")
    parser.add_argument("--workbench", action="store_true", help="启动后打开交易工作台，保留迷你窗口")
    parser.add_argument("--config", type=Path, default=SETTINGS_FILE, help="配置文件路径")
    parser.add_argument("--cache-dir", type=Path, default=ICON_CACHE_DIR, help="图标缓存目录")
    parser.add_argument("--quit-after", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    app = create_application([sys.argv[0]])
    from .single_instance import SingleInstance
    # 内部定时退出验收隔离于日常实例；普通启动在当前 Windows 用户内共享进程锁。
    namespace = "qa:" + str(args.config.resolve()) if args.quit_after > 0 else "desktop"
    app.instance = SingleInstance(app, namespace=namespace)
    try:
        command = "settings" if args.settings else "workbench" if args.workbench else "show"
        if not app.instance.acquire(command):
            return 0
        return run_instance(app, args)
    except OSError as exc:
        QMessageBox.warning(None, "启动提示", str(exc))
        return 1
    finally:
        app.instance.close()


def run_instance(app, args):
    fault_file = None
    if args.quit_after > 0:
        # 启动验证专用：捕获无控制台 EXE 的原生崩溃栈，不记录账户资料。
        import faulthandler
        global _diagnostic_file
        fault_file = _diagnostic_file = args.config.with_suffix(".fault.log").open("w", encoding="utf-8")
        faulthandler.enable(file=fault_file)
        faulthandler.dump_traceback_later(15, file=fault_file)
        # 只在内部验收模式检查，避免打包遗漏时被系统字体／英文回退掩盖。
        if not app.inter_font_loaded or app.translator.isEmpty() or app.windowIcon().isNull():
            raise RuntimeError("启动资源验收失败：请检查 Inter 字体、Qt 中文翻译及应用图标。")
    store = SettingsStore(args.config)
    config = store.load()
    activate_theme(config["ui_theme"])
    client = MarketClient(args.cache_dir, parent=app)
    widget = CoinPilotWidget(config, store, client, startup=StartupManager(args.config, args.cache_dir))
    startup_warning = store.warning
    from .cockpit.desktop import DesktopController
    try:
        app.desktop = DesktopController(app, widget, args.config, args.cache_dir)
        startup_warning = "\n".join(part for part in (startup_warning, app.desktop.warning) if part)
    except (OSError, ValueError, sqlite3.Error) as exc:
        startup_warning = "\n".join(part for part in (startup_warning, "工作台启动失败，迷你窗口仍可使用：" + str(exc)) if part)
    try:
        widget.hotkey = GlobalHotkey(widget.toggle_visibility)
        widget.hotkey.change(config["hide_hotkey"])
    except ValueError as exc:
        startup_warning = "\n".join(part for part in (startup_warning, str(exc)) if part)
    widget.show()
    def activate(command):
        if command == "settings":
            widget.show()
            widget.open_settings()
            if widget.settings_dialog is not None:
                widget.settings_dialog.showNormal()
                widget.settings_dialog.raise_()
                widget.settings_dialog.activateWindow()
        elif hasattr(app, "desktop"):
            app.desktop.open_workbench()
        else:
            widget.show()
            widget.ensure_visible()
            widget.raise_()
            widget.activateWindow()
    app.instance.set_handler(activate)
    if args.settings:
        QTimer.singleShot(0, widget.open_settings)
    if args.workbench and hasattr(app, "desktop"):
        QTimer.singleShot(0, app.desktop.open_workbench)
    if startup_warning:
        def show_warning():
            message = QMessageBox(widget)
            message.setWindowTitle("启动提示")
            message.setIcon(QMessageBox.Icon.Warning)
            message.setText(startup_warning)
            message.addButton("知道了", QMessageBox.ButtonRole.AcceptRole)
            message.exec()
        QTimer.singleShot(100, show_warning)
    if args.quit_after > 0:
        QTimer.singleShot(args.quit_after, app.quit)
    result = app.exec()
    widget.close()
    widget.deleteLater()
    # 在 QApplication 仍存活时销毁所有顶层窗口，避免冻结版解释器清理顺序引发原生崩溃。
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    if fault_file is not None:
        faulthandler.cancel_dump_traceback_later()
    return result
