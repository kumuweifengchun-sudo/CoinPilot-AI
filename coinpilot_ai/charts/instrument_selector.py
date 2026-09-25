"""图表合约选择：自选优先，复用已获取的 OKX 合约目录。"""
from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QCompleter, QSizePolicy

from coinpilot_ai.trading.models import instrument_id


class InstrumentSelector(QComboBox):
    instrument_selected = pyqtSignal(str)

    def __init__(self, service, instrument, parent=None):
        super().__init__(parent)
        self.service, self.instrument = service, instrument
        self._choices = []
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumContentsLength(12)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName('图表品种')
        self.lineEdit().setPlaceholderText('搜索币种')
        self.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setMaxVisibleItems(12)
        self.activated.connect(lambda _: self.commit())
        self.lineEdit().returnPressed.connect(self.commit)
        service.updated.connect(self.refresh_choices)
        self.refresh_choices('market')
        self.set_instrument(instrument)

    @staticmethod
    def label(instrument):
        return instrument.removesuffix('-USDT-SWAP') + '/USDT'

    def refresh_choices(self, kind):
        if kind not in ('market', 'settings', 'selection', 'environment'):
            return
        watchlist = self.service.settings['watchlist']
        choices = list(dict.fromkeys([*watchlist, self.instrument, *sorted(self.service.specs)]))
        if choices == self._choices:
            return
        text, cursor = self.currentText(), self.lineEdit().cursorPosition()
        with QSignalBlocker(self):
            self.clear()
            for instrument in choices:
                self.addItem(self.label(instrument), instrument)
                self.setItemData(self.count()-1, instrument, Qt.ItemDataRole.ToolTipRole)
            self.setEditText(text)
            self.lineEdit().setCursorPosition(cursor)
        self._choices = choices

    def set_instrument(self, instrument):
        self.instrument = instrument
        self.refresh_choices('selection')
        with QSignalBlocker(self):
            self.setCurrentIndex(self.findData(instrument))
            self.setEditText(self.label(instrument))
            self.lineEdit().setCursorPosition(0)
        self.setToolTip(f'{instrument} · OKX USDT 永续\n下拉选择或输入币名搜索，回车确认')

    def commit(self):
        value = self.currentText().strip().upper().replace('/', '')
        if value and not value.endswith(('USDT', '-USDT-SWAP')):
            value += 'USDT'
        try:
            instrument = instrument_id(value)
        except ValueError:
            self.set_instrument(self.instrument)
            return
        changed = instrument != self.instrument
        self.set_instrument(instrument)
        if changed:
            self.instrument_selected.emit(instrument)
