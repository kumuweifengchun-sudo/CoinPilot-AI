"""应用级语义主题。外观与行情来源、交易账户相互独立。"""
from dataclasses import dataclass
from string import Template

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from coinpilot_ai.core.paths import resource_path

DEFAULT_THEME_ID = "okx_dark"
LIGHT_THEME_ID = "enterprise_light"
THEME_LABELS = {DEFAULT_THEME_ID: "深色 · 午夜蓝", LIGHT_THEME_ID: "浅色 · 云白"}
SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 16
RADIUS_DEFAULT = 4
RADIUS_LG = 8
TABLE_ROW_HEIGHT = 32
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
    "background": "#0B121F", "surface": "#161C27", "surface_raised": "#1E2738",
    "surface_hover": "#252D3D", "surface_selected": "#0040A2", "field": "#0B121F",
    "border": "#252D3D", "border_strong": "#3D4560", "text": "#EDF0FF",
    "text_secondary": "#C3C6D6", "text_muted": "#9CA3B8", "accent": "#B2C5FF",
    "accent_text": "#051A3E", "focus": "#B2C5FF", "positive": "#22BA88",
    "negative": "#F1636B", "warning": "#E9B45B", "chart_background": "#0B121F",
    "chart_grid": "#252D3D", "chart_axis": "#9CA3B8", "chart_crosshair": "#B2C5FF",
    "volume_up": "#1E806A", "volume_down": "#A74750", "ema_fast": "#E9B45B",
    "ema_slow": "#A0A6F7", "mini_background": "#161C27",
    "mini_border": "#3D4560", "mini_text": "#EDF0FF", "tooltip": "#1E2738",
}))

register_theme(Theme(LIGHT_THEME_ID, {
    "background": "#FAF9FF", "surface": "#FFFFFF", "surface_raised": "#F1F3FF",
    "surface_hover": "#E1E8FF", "surface_selected": "#DAE2FF", "field": "#FFFFFF",
    "border": "#C3C6D6", "border_strong": "#737685", "text": "#051A3E",
    "text_secondary": "#434654", "text_muted": "#535F73", "accent": "#003D9B",
    "accent_text": "#FFFFFF", "focus": "#0052CC", "positive": "#1E7D4A",
    "negative": "#BA1A1A", "warning": "#B45309", "chart_background": "#FFFFFF",
    "chart_grid": "#E1E8FF", "chart_axis": "#535F73", "chart_crosshair": "#003D9B",
    "volume_up": "#1E7D4A", "volume_down": "#BA1A1A", "ema_fast": "#B45309",
    "ema_slow": "#003D9B", "mini_background": "#FFFFFF", "mini_border": "#737685",
    "mini_text": "#051A3E", "tooltip": "#F1F3FF",
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
    if isinstance(app, QApplication):
        app.setPalette(palette())
        app.setStyleSheet(style_sheet())
    if changed:
        events.changed.emit(_active_id)
    return _active_id


_MENU = Template("""
QMenu { background: ${surface_raised}; color: ${text}; border: 1px solid ${border}; border-radius: ${radius_lg}px; padding: ${space_xs}px; }
QMenu::item { padding: 8px 24px 8px 12px; border-radius: 4px; }
QMenu::item:selected { background: ${surface_selected}; color: ${text}; }
QMenu::item:disabled { color: ${text_muted}; }
QMenu::separator { height: 1px; background: ${border}; margin: ${space_xs}px ${space_sm}px; }
QToolTip { color: ${text}; background: ${tooltip}; border: 1px solid ${border_strong}; padding: ${space_sm}px; }
""")

_STYLE = Template("""
QMainWindow, QDialog, QWidget#workbenchRoot { background: ${background}; }
QDockWidget { border: 1px solid ${border}; }
QDockWidget::title { background: ${surface_raised}; padding: 6px; color: ${text_secondary}; }
QWidget#dockTitleBar { background: ${surface_raised}; }
QWidget#dockTitleBar QToolButton { padding: 0; border: none; border-radius: 3px; background: transparent; }
QWidget#dockTitleBar QToolButton:hover { background: ${surface_hover}; }
QWidget#dockTitleBar QToolButton:pressed { background: ${surface_selected}; }
QMainWindow::separator { background: ${border}; width: ${space_xs}px; height: ${space_xs}px; }
QMainWindow::separator:hover { background: ${border_strong}; }
QWidget { color: ${text_secondary}; font-family: 'Inter Variable', 'Microsoft YaHei UI'; font-size: 12px; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 20px; font-weight: 600; color: ${text}; }
QLabel#muted { color: ${text_muted}; }
QLabel#sectionTitle { font-size: 13px; font-weight: 600; color: ${text}; padding: 4px 0; }
QWidget#sidePanel { background: ${surface}; border: 1px solid ${border}; border-radius: 8px; }
QWidget#tradeFormContent { background: ${surface}; }
QTabWidget::pane { border: 1px solid ${border}; background: ${surface}; border-radius: 6px; top: -1px; }
QTabBar { background: transparent; }
QTabBar::tab { background: transparent; padding: ${space_sm}px ${space_md}px; margin-right: ${space_xs}px; color: ${text_muted}; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: ${text}; background: ${surface_hover}; }
QTabBar::tab:selected { color: ${accent}; border-bottom-color: ${accent}; background: ${surface_raised}; }
QTabWidget#navigation::pane { border: 0; background: ${surface}; }
QTabWidget#navigation > QTabBar::tab { padding: 10px 22px; font-size: 13px; font-weight: 600; }
QGroupBox { background: ${surface}; border: 1px solid ${border}; border-radius: ${radius_lg}px; margin-top: ${space_md}px; padding: ${space_sm}px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: ${text_secondary}; }
QLineEdit, QTextEdit, QTextBrowser, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {
 background: ${field}; color: ${text}; border: 1px solid ${border}; border-radius: 6px; padding: ${space_xs}px ${space_sm}px;
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
QPushButton, QToolButton { background: ${surface_raised}; border: 1px solid ${border}; border-radius: 6px; padding: ${space_sm}px ${space_sm}px; color: ${text_secondary}; }
QPushButton:hover, QToolButton:hover { background: ${surface_hover}; border-color: ${border_strong}; color: ${text}; }
QPushButton:pressed, QToolButton:pressed { background: ${surface_selected}; }
QPushButton:focus, QToolButton:focus { border-color: ${focus}; }
QPushButton:checked, QToolButton:checked { background: ${surface_selected}; border-color: ${border_strong}; color: ${text}; }
QPushButton#primary { background: ${accent}; border-color: ${accent}; color: ${accent_text}; font-weight: 600; }
QPushButton#primary:hover { background: ${focus}; border-color: ${focus}; }
QPushButton#primary:pressed { background: ${surface_selected}; color: ${text}; border-color: ${focus}; }
QPushButton#primary:focus { border-color: ${text}; }
QPushButton:disabled, QPushButton#primary:disabled, QToolButton:disabled { color: ${text_muted}; background: ${surface}; border-color: ${border}; }
QCheckBox, QRadioButton { spacing: ${space_sm}px; background: transparent; min-height: 18px; }
QCheckBox::indicator, QGroupBox::indicator { width: 14px; height: 14px; background: ${field}; border: 1px solid ${border_strong}; border-radius: 4px; }
QCheckBox::indicator:checked, QGroupBox::indicator:checked { background: ${surface_selected}; border-color: ${accent}; image: url(${assets}/check.svg); }
QGroupBox::indicator { width: 14px; height: 14px; background: transparent; border: none; image: url(${assets}/chevron-right.svg); }
QGroupBox::indicator:checked { background: transparent; border: none; image: url(${assets}/chevron-down.svg); }
QCheckBox::indicator:hover { border-color: ${focus}; }
QCheckBox:disabled { color: ${text_muted}; }
QTableWidget, QListWidget { background: ${surface}; alternate-background-color: ${surface_raised}; border: 1px solid ${border}; border-radius: 6px; outline: 0; }
QTableWidget::item { padding: ${space_xs}px ${space_sm}px; border: none; border-bottom: 1px solid ${border}; }
QListWidget::item { padding: ${space_sm}px; border-radius: 4px; }
QTableWidget::item:selected, QListWidget::item:selected { background: ${surface_selected}; color: ${text}; }
QTableWidget::item:hover:!selected, QListWidget::item:hover:!selected { background: ${surface_hover}; }
QHeaderView { background: ${surface_raised}; }
QHeaderView::section { background: ${surface_raised}; color: ${text_secondary}; font-weight: 600; padding: ${space_sm}px; border: none; border-bottom: 1px solid ${border}; }
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
QWidget#dockTitleBar QLabel { color: ${text}; font-weight: 600; }
QListWidget#settingsNavigation { background: ${surface}; border: none; padding: ${space_sm}px; }
QListWidget#settingsNavigation::item { padding: 12px ${space_sm}px; margin-bottom: ${space_xs}px; }
QListWidget#settingsNavigation::item:selected { background: ${surface_selected}; color: ${text}; }
QLabel#workbenchStatus { color: ${text_muted}; padding: ${space_xs}px ${space_sm}px; }
QTabBar::tab:disabled { color: ${text_muted}; background: ${surface}; }
QTextEdit:disabled, QPlainTextEdit:disabled { color: ${text_muted}; background: ${surface}; }
QRadioButton::indicator { width: 16px; height: 16px; border: 1px solid ${border_strong}; border-radius: 8px; background: ${field}; }
QRadioButton::indicator:checked { background: ${accent}; border-color: ${accent}; }
QRadioButton::indicator:hover, QCheckBox:focus, QRadioButton:focus { color: ${accent}; }
QRadioButton:disabled { color: ${text_muted}; }
QProgressBar { background: ${surface_raised}; border: 1px solid ${border}; border-radius: ${radius_default}px; text-align: center; color: ${text}; }
QProgressBar::chunk { background: ${surface_selected}; border-radius: ${radius_default}px; }

""")


_METRICS = {"space_xs": str(SPACING_XS), "space_sm": str(SPACING_SM),
            "space_md": str(SPACING_MD), "radius_default": str(RADIUS_DEFAULT),
            "radius_lg": str(RADIUS_LG)}


def menu_style(theme_id: str | None = None) -> str:
    return _MENU.substitute(get_theme(theme_id).colors | _METRICS)


def style_sheet(theme_id: str | None = None) -> str:
    return _STYLE.substitute(get_theme(theme_id).colors | _METRICS | {"assets": _ASSETS}) + menu_style(theme_id)


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
