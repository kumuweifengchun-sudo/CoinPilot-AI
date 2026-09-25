"""中文设置对话框；草稿与生效配置分离。"""

from decimal import Decimal

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from coinpilot_ai.core.config import DEFAULT_CONFIG, PROXY_LABELS, SOURCE_LABELS, normalize_hotkey, normalize_proxy_host, normalize_symbol
from coinpilot_ai.desktop.visuals import Quote, draw_ticker, ticker_size
from coinpilot_ai.ui.typography import font, render_hints
from coinpilot_ai.ui.icons import icon, set_button_icon
from coinpilot_ai.ui.theme import color, events, style_sheet


class Preview(QWidget):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config.copy()
        self.setMinimumHeight(76)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        quote = Quote(self.config["symbol1"] or "BTCUSDT", Decimal("68432.18"))
        precision = self.config["decimals1"]
        mini = self.config.get("mini_mode", True)
        width, height = ticker_size([quote], [precision], self.config["text_size"], mini=mini, device=self)
        painter = QPainter(self)
        render_hints(painter)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color("background")))
        painter.drawRoundedRect(QRectF(self.rect()), 12, 12)
        if mini:
            painter.setFont(font(10))
            painter.setPen(QColor(color("text_muted")))
            painter.drawText(QRectF(10, 4, self.width() - 20, 16), Qt.AlignmentFlag.AlignLeft, "示例行情 · 迷你预览")
        scale = min((self.width() - 24) / width, (self.height() - 16) / height, 1)
        painter.translate((self.width() - width * scale) / 2, (self.height() - height * scale) / 2)
        painter.scale(scale, scale)
        draw_ticker(painter, QRectF(0, 0, width, height), quote, precision,
                    self.config["text_size"], self.config["bg_opacity"], sample=True, mini=mini)
        painter.end()


class SettingsDialog(QDialog):
    def __init__(self, owner, parent=None, *, embedded=False):
        super().__init__(parent or owner)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.owner = owner
        self.draft = owner.config.copy()
        self.symbol_edits = []
        self.decimal_spins = []
        self.number_inputs = {}
        self._card_icons = []
        self._full_text_size = self.draft["text_size"]
        self.setWindowTitle("CoinPilot AI · 迷你窗口设置")
        self.setModal(not embedded)
        self.setMinimumSize(320, 320)
        self._build_ui()
        self._load_startup()
        self._apply_style()
        events.changed.connect(self._theme_changed)
        self._refresh_preview()
        self.owner.client.icons_finished.connect(self._icons_finished)
        area = self.screen().availableGeometry()
        self.resize(min(400, area.width() - 32), min(560, area.height() - 64))

    def _card(self, layout, title, toggle=None, extra_toggle=None):
        frame = QFrame()
        frame.setObjectName("card")
        body = QVBoxLayout(frame)
        body.setContentsMargins(12, 8, 12, 8)
        body.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        heading = QHBoxLayout()
        accent = QLabel()
        accent.setFixedSize(16, 16)
        name = {"币种与数据源": "chart-candlestick", "外观设置": "sliders-horizontal", "常规设置": "settings", "网络代理": "activity"}.get(title, "settings")
        accent.setPixmap(icon(name, "@text_secondary").pixmap(16, 16))
        self._card_icons.append((accent, name))
        heading.addWidget(accent)
        heading.addWidget(label)
        heading.addStretch()
        if extra_toggle is not None:
            heading.addWidget(extra_toggle)
        if toggle is not None:
            heading.addWidget(toggle)
        body.addLayout(heading)
        layout.addWidget(frame)
        return body

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("content")
        body = QVBoxLayout(content)
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(10)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        title = QLabel("偏好设置")
        title.setObjectName("pageTitle")
        subtitle = QLabel("自定义你的桌面行情空间")
        subtitle.setObjectName("muted")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        badge = QLabel("MARKET / WIDGET")
        badge.setObjectName("brandBadge")
        header.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addLayout(header)

        self.source_combo = QComboBox()
        for source, label in SOURCE_LABELS.items():
            self.source_combo.addItem(label, source)
        self.source_combo.setCurrentIndex(self.source_combo.findData(self.draft.get("price_source", "auto")))
        self.source_combo.setAccessibleName("行情数据源")
        self.source_combo.setToolTip("均为永续合约报价；自动模式按 Binance、OKX、Bybit 顺序尝试")
        coins = self._card(body, "币种与数据源", toggle=self.source_combo)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(5)
        for column, text in enumerate(("序号", "交易对", "小数位")):
            label = QLabel(text)
            label.setObjectName("muted")
            grid.addWidget(label, 0, column)
        grid.setColumnStretch(1, 1)
        for i in range(1, 4):
            index = QLabel(f"0{i}")
            index.setObjectName("index")
            edit = QLineEdit(self.draft[f"symbol{i}"])
            edit.setPlaceholderText("例如 BTCUSDT")
            edit.setToolTip("统一填写 BTCUSDT 等合约交易对，系统会自动转换各交易所代码")
            edit.setMaxLength(30)
            edit.setAccessibleName(f"交易对 {i}")
            edit.editingFinished.connect(lambda edit=edit: edit.setText(edit.text().strip().upper()))
            edit.textChanged.connect(self._refresh_preview)
            spin = QSpinBox()
            spin.setRange(0, 8)
            spin.setValue(self.draft[f"decimals{i}"])
            spin.setFixedWidth(56)
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.setAccessibleName(f"交易对 {i} 小数位")
            spin.valueChanged.connect(self._refresh_preview)
            self.symbol_edits.append(edit)
            self.decimal_spins.append(spin)
            grid.addWidget(index, i, 0)
            grid.addWidget(edit, i, 1)
            grid.addWidget(spin, i, 2)
        coins.addLayout(grid)

        self.mini_check = QCheckBox("迷你模式")
        self.mini_check.setChecked(self.draft.get("mini_mode", True))
        self.mini_check.setToolTip("仅显示图标与价格，固定 12 像素字号；关闭后可调节完整模式字号")
        self.preview_check = QCheckBox("预览")
        appearance = self._card(body, "外观设置", toggle=self.mini_check, extra_toggle=self.preview_check)
        self.preview = Preview(self.draft)
        appearance.addWidget(self.preview)
        self.preview.hide()
        self.preview_check.toggled.connect(self.preview.setVisible)
        self.preview.setToolTip("示例行情；预览按可用空间缩放，保存后应用到桌面")
        self._number_input(appearance, "text_size", "字号", 8, 64, "像素", self.draft["text_size"])
        self._number_input(appearance, "bg_opacity", "背景不透明度", 0, 100, "%", round(self.draft["bg_opacity"] * 100))
        self.mini_check.toggled.connect(self._toggle_mini)
        self._toggle_mini(self.mini_check.isChecked())

        self.cycle_check = QCheckBox("自动轮播币种")
        self.cycle_check.setChecked(self.draft["cycle_enabled"])
        self.startup_check = QCheckBox("开机自启动")
        self.startup_check.setToolTip("登录 Windows 后自动启动；保存设置后生效，仅对当前用户有效")
        behavior = self._card(body, "常规设置")
        toggles = QHBoxLayout()
        toggles.addWidget(self.startup_check)
        toggles.addSpacing(12)
        toggles.addWidget(self.cycle_check)
        toggles.addStretch()
        behavior.addLayout(toggles)
        self._number_input(behavior, "update_interval", "行情兜底间隔", 5, 300, "秒", self.draft["update_interval"])
        self.number_inputs["update_interval"].setToolTip("正常时实时接收行情；连接异常时按此间隔查询备用行情")
        self._number_input(behavior, "cycle_interval", "币种轮播间隔", 3, 60, "秒", self.draft["cycle_interval"])
        self.cycle_check.toggled.connect(self._toggle_cycle)
        self._toggle_cycle(self.cycle_check.isChecked())
        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(QLabel("隐藏／显示快捷键"))
        shortcut_row.addStretch()
        self.hotkey_edit = QLineEdit(self.draft.get("hide_hotkey", "Alt+Z"))
        self.hotkey_edit.setFixedWidth(110)
        self.hotkey_edit.setAccessibleName("隐藏／显示快捷键")
        self.hotkey_edit.setToolTip("全局生效；例如 Alt+Z、Ctrl+Shift+H，保存后应用")
        shortcut_row.addWidget(self.hotkey_edit)
        behavior.addLayout(shortcut_row)

        self.auto_update_check = QCheckBox("自动检查正式版更新")
        self.auto_update_check.setChecked(self.draft.get("auto_check_updates", True))
        self.auto_update_check.setToolTip("安装版启动后检查，之后每 6 小时检查；下载与退出安装由你决定")
        updates = self._card(body, "软件更新", toggle=self.auto_update_check)
        from coinpilot_ai.core.version import VERSION
        update_row = QHBoxLayout()
        update_row.addWidget(QLabel(f"当前版本 {VERSION}"))
        update_row.addStretch()
        self.check_update_button = QPushButton("检查更新")
        self.check_update_button.setAutoDefault(False)
        self.check_update_button.clicked.connect(self.owner.update_requested.emit)
        update_row.addWidget(self.check_update_button)
        updates.addLayout(update_row)

        self.proxy_check = QCheckBox("启用代理")
        self.proxy_check.setChecked(self.draft.get("proxy_enabled", True))
        proxy_card = self._card(body, "网络代理", toggle=self.proxy_check)
        self.proxy_type_combo = QComboBox()
        for protocol, label in PROXY_LABELS.items():
            self.proxy_type_combo.addItem(label, protocol)
        self.proxy_type_combo.setCurrentIndex(self.proxy_type_combo.findData(self.draft.get("proxy_type", "socks5")))
        self.proxy_host_edit = QLineEdit(self.draft.get("proxy_host", DEFAULT_CONFIG["proxy_host"]))
        self.proxy_host_edit.setPlaceholderText("127.0.0.1")
        self.proxy_host_edit.setMinimumWidth(0)
        self.proxy_host_edit.setToolTip("仅填写 IP 地址或主机名，不含协议和端口")
        self.proxy_port_spin = QSpinBox()
        self.proxy_port_spin.setRange(1, 65535)
        self.proxy_port_spin.setValue(self.draft.get("proxy_port", DEFAULT_CONFIG["proxy_port"]))
        self.proxy_port_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.proxy_port_spin.setFixedWidth(65)
        proxy_row = QHBoxLayout()
        for control, title in ((self.proxy_type_combo, "代理协议"), (self.proxy_host_edit, "代理地址"),
                               (self.proxy_port_spin, "代理端口")):
            control.setAccessibleName(title)
            control.setEnabled(self.proxy_check.isChecked())
            self.proxy_check.toggled.connect(control.setEnabled)
            proxy_row.addWidget(control, 1 if control is self.proxy_host_edit else 0)
        proxy_card.addLayout(proxy_row)
        hint = QLabel("行情、图标和软件更新使用此代理，保存后生效；关闭后直连。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        proxy_card.addWidget(hint)
        body.addStretch()

        footer = QWidget()
        footer.setObjectName("footer")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(12, 10, 12, 12)
        self.feedback = QLabel("")
        self.feedback.setObjectName("feedback")
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        footer_layout.addWidget(self.feedback)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.refresh_button = QPushButton("重新加载图标")
        set_button_icon(self.refresh_button, "refresh-cw", size=14)
        self.refresh_button.setObjectName("quiet")
        self.refresh_button.setToolTip("立即清理已保存币种的图标缓存并重新下载")
        self.refresh_button.clicked.connect(self._refresh_icons)
        cancel = QPushButton("撤销未保存修改" if self.embedded else "取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存设置")
        set_button_icon(save, "check", size=14, color="@accent_text")
        save.setObjectName("primary")
        save.setDefault(not self.embedded)
        save.clicked.connect(self._save)
        for button in (self.refresh_button, cancel, save):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setAutoDefault(False)
        cancel.setAutoDefault(False)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        footer_layout.addLayout(buttons)
        root.addWidget(footer)

    def _load_startup(self):
        manager = self.owner.startup
        if manager is None or not manager.supported:
            self.startup_check.setEnabled(False)
            self.startup_check.setToolTip("当前运行环境不支持开机自启动")
            return
        try:
            self.startup_check.setChecked(manager.read() is not None)
        except OSError as exc:
            self.startup_check.setEnabled(False)
            self._message(str(exc))

    def _number_input(self, layout, key, title, low, high, unit, initial):
        row = QHBoxLayout()
        label = QLabel(title)
        number = QSpinBox()
        number.setAccessibleName(title)
        number.setRange(low, high)
        number.setSuffix(f" {unit}")
        number.setFixedWidth(90)
        number.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        number.setKeyboardTracking(False)
        number.setValue(initial)
        label.setBuddy(number)
        self.number_inputs[key] = number
        number.valueChanged.connect(self._refresh_preview)
        row.addWidget(label)
        row.addStretch()
        row.addWidget(number)
        layout.addLayout(row)

    def _toggle_mini(self, enabled):
        number = self.number_inputs["text_size"]
        number.interpretText()
        if enabled and number.isEnabled():
            self._full_text_size = number.value()
        number.setEnabled(not enabled)
        number.setValue(12 if enabled else self._full_text_size)
        number.setToolTip("迷你模式使用固定 12 像素字号" if enabled else "完整模式的文字大小")
        self._refresh_preview()

    def _toggle_cycle(self, enabled):
        self.number_inputs["cycle_interval"].setEnabled(enabled)

    def _refresh_preview(self, *_):
        if not hasattr(self, "preview"):
            return
        draft = self.draft.copy()
        draft["mini_mode"] = self.mini_check.isChecked()
        for i, (edit, spin) in enumerate(zip(self.symbol_edits, self.decimal_spins), 1):
            draft[f"symbol{i}"] = edit.text().strip().upper()
            draft[f"decimals{i}"] = spin.value()
        for key, number in self.number_inputs.items():
            draft[key] = number.value() / 100 if key == "bg_opacity" else number.value()
        if draft["mini_mode"]:
            draft["text_size"] = self._full_text_size
        self.preview.config = draft
        self.preview.update()

    def _message(self, text):
        self.feedback.setText(text)
        self.feedback.show()

    def _save(self):
        candidate = self.owner.config.copy()  # 保留对话框打开期间的最新位置。
        for i, (edit, spin) in enumerate(zip(self.symbol_edits, self.decimal_spins), 1):
            try:
                candidate[f"symbol{i}"] = normalize_symbol(edit.text())
            except ValueError as exc:
                self._message(f"第 {i} 个交易对：{exc}")
                edit.setFocus()
                return
            spin.interpretText()
            candidate[f"decimals{i}"] = spin.value()
        for key, number in self.number_inputs.items():
            number.interpretText()
            candidate[key] = number.value() / 100 if key == "bg_opacity" else number.value()
        if self.mini_check.isChecked():
            candidate["text_size"] = self._full_text_size
        candidate["cycle_enabled"] = self.cycle_check.isChecked()
        candidate["mini_mode"] = self.mini_check.isChecked()
        candidate["price_source"] = self.source_combo.currentData()
        candidate["auto_check_updates"] = self.auto_update_check.isChecked()
        try:
            candidate["proxy_host"] = normalize_proxy_host(self.proxy_host_edit.text())
        except ValueError as exc:
            self._message(str(exc))
            self.proxy_host_edit.setFocus()
            return
        self.proxy_port_spin.interpretText()
        candidate["proxy_enabled"] = self.proxy_check.isChecked()
        candidate["proxy_type"] = self.proxy_type_combo.currentData()
        candidate["proxy_port"] = self.proxy_port_spin.value()
        try:
            candidate["hide_hotkey"] = normalize_hotkey(self.hotkey_edit.text())
            startup_enabled = self.startup_check.isChecked() if self.startup_check.isEnabled() else None
            self.owner.save_configuration(candidate, startup_enabled=startup_enabled)
        except ValueError as exc:
            self._message(str(exc))
            self.hotkey_edit.setFocus()
            return
        except OSError as exc:
            self._message(f"保存失败，请检查权限后重试。{exc}")
            return
        self.owner.apply_settings(candidate)
        self.accept()

    def accept(self):
        if not self.embedded:
            return super().accept()
        self.draft = self.owner.config.copy()
        self._message("设置已保存并生效")

    def reject(self):
        if not self.embedded:
            return super().reject()
        self.draft = self.owner.config.copy()
        self._full_text_size = self.draft["text_size"]
        for i, (edit, spin) in enumerate(zip(self.symbol_edits, self.decimal_spins), 1):
            edit.setText(self.draft[f"symbol{i}"])
            spin.setValue(self.draft[f"decimals{i}"])
        self.mini_check.blockSignals(True)
        self.mini_check.setChecked(self.draft["mini_mode"])
        self.mini_check.blockSignals(False)
        for key, number in self.number_inputs.items():
            number.setValue(round(self.draft[key]*100) if key == "bg_opacity" else self.draft[key])
        self.number_inputs["text_size"].setEnabled(not self.draft["mini_mode"])
        self.number_inputs["text_size"].setValue(12 if self.draft["mini_mode"] else self._full_text_size)
        self.cycle_check.setChecked(self.draft["cycle_enabled"])
        self.source_combo.setCurrentIndex(self.source_combo.findData(self.draft["price_source"]))
        self.auto_update_check.setChecked(self.draft.get("auto_check_updates", True))
        self.proxy_check.setChecked(self.draft["proxy_enabled"])
        self.proxy_type_combo.setCurrentIndex(self.proxy_type_combo.findData(self.draft["proxy_type"]))
        self.proxy_host_edit.setText(self.draft["proxy_host"])
        self.proxy_port_spin.setValue(self.draft["proxy_port"])
        self.hotkey_edit.setText(self.draft["hide_hotkey"])
        self._load_startup()
        self._refresh_preview()
        self._message("已恢复已保存设置")

    def _refresh_icons(self):
        self.refresh_button.setEnabled(False)
        self._message("正在重新加载已保存币种的图标…")
        try:
            self.owner.reload_icons(clear=True)
        except OSError as exc:
            self.refresh_button.setEnabled(True)
            self._message(f"无法清理图标缓存：{exc}")

    def _icons_finished(self, success, failure):
        if self.refresh_button.isEnabled():
            return
        self.refresh_button.setEnabled(True)
        self._message(f"图标加载完成：{success} 个成功，{failure} 个使用默认图标。")

    def _theme_changed(self, _theme_id):
        self._apply_style()
        for label, name in self._card_icons:
            label.setPixmap(icon(name, "@text_secondary").pixmap(16, 16))
        self.preview.update()

    def _apply_style(self):
        c = {name: color(name) for name in (
            "background", "surface", "surface_raised", "surface_hover", "field", "border",
            "border_strong", "text", "text_secondary", "text_muted", "accent", "accent_text",
            "focus", "warning")}
        self.setStyleSheet(style_sheet() + """
            QDialog, QWidget#content { background: %(background)s; }
            QLabel#pageTitle { color: %(text)s; font-size: 19px; font-weight: 600; }
            QLabel#brandBadge { color: %(text)s; font-size: 9px; font-weight: 600;
                border: 1px solid %(border_strong)s; border-radius: 5px;
                background: %(surface_raised)s; padding: 5px 7px; }
            QLabel#sectionAccent { background: %(accent)s; border-radius: 1px; }
            QLabel#sectionTitle { font-size: 12px; font-weight: 600; color: %(text)s; }
            QLabel#muted { color: %(text_muted)s; font-size: 10px; }
            QLabel#index { color: %(text_muted)s; font-weight: 600; }
            QFrame#card { background: %(surface)s; border: 1px solid %(border)s; border-radius: 9px; }
            QWidget#footer { background: %(surface)s; border-top: 1px solid %(border)s; }
            QLabel#feedback { color: %(warning)s; font-size: 11px; padding: 4px 0; }
            QPushButton#quiet { background: transparent; border-color: transparent;
                color: %(text_secondary)s; padding-left: 0; padding-right: 0; }
            QPushButton#quiet:hover { color: %(text)s; }
        """ % c)
