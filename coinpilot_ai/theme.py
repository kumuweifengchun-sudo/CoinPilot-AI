"""应用级语义主题。外观与行情来源、交易账户相互独立。"""
from dataclasses import dataclass
from string import Template

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from .visuals import resource_path

DEFAULT_THEME_ID = "okx_dark"
_ASSETS = resource_path("coinpilot_ai/assets/lucide").as_posix()


@dataclass(frozen=True)
class Theme:
    id: str
    colors: dict[str, str]

    def color(self, name: str) -> str:
        return self.colors[name]


_REQUIRED = frozenset((
    "background", "surface", "surface_raised", "surface_hover", "surface_selected",
    "field", "border", "border_strong", "text", "text_secondary", "text_muted",
    "accent", "accent_text", "focus", "positive", "negative", "warning",
    "chart_background", "chart_grid", "chart_axis", "chart_crosshair", "volume_up",
    "volume_down", "ema_fast", "ema_slow", "mini_background", "mini_border",
    "mini_text", "tooltip",
))
_registry: dict[str, Theme] = {}


def register_theme(theme: Theme) -> None:
    missing = _REQUIRED - theme.colors.keys()
    if missing:
        raise ValueError("主题缺少颜色令牌：" + ", ".join(sorted(missing)))
    if theme.id in _registry:
        raise ValueError("主题已存在：" + theme.id)
    _registry[theme.id] = theme


register_theme(Theme(DEFAULT_THEME_ID, {
    "background": "#0B0D10", "surface": "#121519", "surface_raised": "#1A1E23",
    "surface_hover": "#23282E", "surface_selected": "#2B3138", "field": "#101317",
    "border": "#2B3036", "border_strong": "#454C54", "text": "#F3F5F6",
    "text_secondary": "#BEC5CC", "text_muted": "#89939D", "accent": "#F3F5F6",
    "accent_text": "#101317", "focus": "#CAD4DE", "positive": "#22BA88",
    "negative": "#F1636B", "warning": "#E9B45B", "chart_background": "#111419",
    "chart_grid": "#262C32", "chart_axis": "#929CA6", "chart_crosshair": "#9EA8B3",
    "volume_up": "#1E806A", "volume_down": "#A74750", "ema_fast": "#E9B45B",
    "ema_slow": "#A0A6F7", "mini_background": "#121519",
    "mini_border": "#535B63", "mini_text": "#F3F5F6", "tooltip": "#22272D",
}))


class _ThemeEvents(QObject):
    changed = pyqtSignal(str)


events = _ThemeEvents()
_active_id = DEFAULT_THEME_ID


def get_theme(theme_id: str | None = None) -> Theme:
    return _registry.get(theme_id or _active_id, _registry[DEFAULT_THEME_ID])


def color(name: str) -> str:
    return get_theme().color(name)


def palette() -> QPalette:
    c = get_theme().colors
    result = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "background"), (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.Base, "field"), (QPalette.ColorRole.AlternateBase, "surface"),
                      (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "surface_raised"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "surface_selected"),
                      (QPalette.ColorRole.HighlightedText, "text"),
                      (QPalette.ColorRole.PlaceholderText, "text_muted"),
                      (QPalette.ColorRole.ToolTipBase, "tooltip"),
                      (QPalette.ColorRole.ToolTipText, "text"),
                      (QPalette.ColorRole.BrightText, "text")):
        result.setColor(role, QColor(c[key]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["text_muted"]))
    return result


def activate_theme(theme_id: str) -> str:
    """选择主题并通知已打开的窗口与自绘组件。未知值回退默认主题。"""
    global _active_id
    selected = get_theme(theme_id).id
    changed = selected != _active_id
    _active_id = selected
    app = QApplication.instance()
    if app is not None:
        app.setPalette(palette())
        if changed:
            app.setStyleSheet(style_sheet())
    if changed:
        events.changed.emit(_active_id)
    return _active_id


_MENU = Template("""
QMenu { background: ${surface_raised}; color: ${text}; border: 1px solid ${border}; border-radius: 7px; padding: 5px; }
QMenu::item { padding: 8px 24px 8px 12px; border-radius: 4px; }
QMenu::item:selected { background: ${surface_selected}; color: ${text}; }
QMenu::item:disabled { color: ${text_muted}; }
QMenu::separator { height: 1px; background: ${border}; margin: 5px 9px; }
QToolTip { color: ${text}; background: ${tooltip}; border: 1px solid ${border_strong}; padding: 6px; }
""")

_STYLE = Template("""
QMainWindow, QDialog, QWidget#workbenchRoot { background: ${background}; }
QWidget { color: ${text_secondary}; font-family: 'Inter Variable', 'Microsoft YaHei UI'; font-size: 12px; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 18px; font-weight: 600; color: ${text}; }
QLabel#muted { color: ${text_muted}; }
QLabel#eyebrow { color: ${text_muted}; font-size: 10px; letter-spacing: 2px; }
QLabel#sectionTitle { font-size: 13px; font-weight: 600; color: ${text}; padding: 4px 0; }
QLabel#brandMark { background: ${surface_raised}; border: 1px solid ${border}; border-radius: 8px; }
QWidget#sidePanel { background: ${surface}; border: 1px solid ${border}; border-radius: 8px; }
QWidget#tradeFormContent { background: ${surface}; }
QTabWidget::pane { border: 1px solid ${border}; background: ${surface}; border-radius: 6px; top: -1px; }
QTabBar { background: transparent; }
QTabBar::tab { background: transparent; padding: 9px 15px; margin-right: 3px; color: ${text_muted}; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: ${text}; background: ${surface_hover}; }
QTabBar::tab:selected { color: ${text}; border-bottom-color: ${accent}; background: ${surface_raised}; }
QTabWidget#navigation::pane { border: 0; background: ${surface}; }
QTabWidget#navigation > QTabBar::tab { padding: 10px 22px; font-size: 13px; font-weight: 600; }
QGroupBox { background: ${surface}; border: 1px solid ${border}; border-radius: 7px; margin-top: 10px; padding: 10px 8px 7px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: ${text_secondary}; }
QLineEdit, QTextEdit, QTextBrowser, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {
 background: ${field}; color: ${text}; border: 1px solid ${border}; border-radius: 6px; padding: 5px 8px;
 selection-background-color: ${surface_selected}; selection-color: ${text}; }
QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover { border-color: ${border_strong}; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QAbstractSpinBox:focus { border-color: ${focus}; }
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled { color: ${text_muted}; background: ${surface}; border-color: ${border}; }
QComboBox, QDateTimeEdit { padding-right: 25px; }
QComboBox::drop-down, QDateTimeEdit::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 24px; border: none; }
QComboBox::down-arrow, QDateTimeEdit::down-arrow { image: url(${assets}/chevron-down.svg); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: ${surface_raised}; color: ${text}; border: 1px solid ${border_strong}; selection-background-color: ${surface_selected}; outline: 0; padding: 4px; }
QAbstractSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 18px; border: 0; }
QAbstractSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; border: 0; }
QAbstractSpinBox::up-arrow { image: url(${assets}/chevron-up.svg); width: 10px; height: 10px; }
QAbstractSpinBox::down-arrow { image: url(${assets}/chevron-down.svg); width: 10px; height: 10px; }
QPushButton, QToolButton { background: ${surface_raised}; border: 1px solid ${border}; border-radius: 6px; padding: 6px 10px; color: ${text_secondary}; }
QPushButton:hover, QToolButton:hover { background: ${surface_hover}; border-color: ${border_strong}; color: ${text}; }
QPushButton:pressed, QToolButton:pressed { background: ${surface_selected}; }
QPushButton:focus, QToolButton:focus { border-color: ${focus}; }
QPushButton:checked, QToolButton:checked { background: ${surface_selected}; border-color: ${border_strong}; color: ${text}; }
QPushButton#primary { background: ${accent}; border-color: ${accent}; color: ${accent_text}; font-weight: 600; }
QPushButton#primary:hover { background: ${text_secondary}; border-color: ${text_secondary}; }
QPushButton:disabled, QPushButton#primary:disabled, QToolButton:disabled { color: ${text_muted}; background: ${surface}; border-color: ${border}; }
QCheckBox, QRadioButton { spacing: 7px; background: transparent; min-height: 18px; }
QCheckBox::indicator, QGroupBox::indicator { width: 14px; height: 14px; background: ${field}; border: 1px solid ${border_strong}; border-radius: 4px; }
QCheckBox::indicator:checked, QGroupBox::indicator:checked { background: ${surface_selected}; border-color: ${accent}; image: url(${assets}/check.svg); }
QGroupBox::indicator { width: 14px; height: 14px; background: transparent; border: none; image: url(${assets}/chevron-right.svg); }
QGroupBox::indicator:checked { background: transparent; border: none; image: url(${assets}/chevron-down.svg); }
QCheckBox::indicator:hover { border-color: ${focus}; }
QCheckBox:disabled { color: ${text_muted}; }
QTableWidget, QListWidget { background: ${surface}; alternate-background-color: ${surface_raised}; border: 1px solid ${border}; border-radius: 6px; outline: 0; }
QTableWidget::item { padding: 5px 7px; border: none; border-bottom: 1px solid ${border}; }
QListWidget::item { padding: 10px 8px; border-radius: 4px; }
QTableWidget::item:selected, QListWidget::item:selected { background: ${surface_selected}; color: ${text}; }
QTableWidget::item:hover, QListWidget::item:hover { background: ${surface_hover}; }
QHeaderView { background: ${surface_raised}; }
QHeaderView::section { background: ${surface_raised}; color: ${text_secondary}; font-weight: 500; padding: 7px; border: none; border-bottom: 1px solid ${border}; }
QTableCornerButton::section { background: ${surface_raised}; border: none; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 6px; background: transparent; margin: 2px 0; }
QScrollBar:horizontal { height: 6px; background: transparent; margin: 0 2px; }
QScrollBar::handle { background: ${border_strong}; border-radius: 3px; }
QScrollBar::handle:vertical { min-height: 28px; }
QScrollBar::handle:horizontal { min-width: 28px; }
QScrollBar::handle:hover { background: ${text_muted}; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: ${border}; }
QSplitter::handle:hover { background: ${border_strong}; }
""")


def menu_style(theme_id: str | None = None) -> str:
    return _MENU.substitute(get_theme(theme_id).colors)


def style_sheet(theme_id: str | None = None) -> str:
    return _STYLE.substitute(get_theme(theme_id).colors | {"assets": _ASSETS}) + menu_style(theme_id)


def chart_controls_style() -> str:
    c = get_theme().colors
    return ("QToolButton {background:%s; border:0; border-radius:5px; padding:4px;} "
            "QToolButton:hover {background:%s;} QToolButton:checked {background:%s;} "
            "QPushButton#chartButton {padding:3px 5px; min-height:20px; background:%s; border:0;} "
            "QPushButton#chartButton:hover {background:%s;} "
            "QPushButton#chartButton:checked {color:%s; background:%s;} "
            "QPushButton#chartButton::menu-indicator {width:0;}" %
            (c["surface"], c["surface_hover"], c["surface_selected"], c["surface"],
             c["surface_hover"], c["text"], c["surface_selected"]))


# 兼容旧导入；运行时使用 style_sheet()/menu_style()。
STYLE = style_sheet(DEFAULT_THEME_ID)
MENU_STYLE = menu_style(DEFAULT_THEME_ID)
