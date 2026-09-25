"""日常模拟、交易所与训练账户共用的交易注释编辑。"""
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                             QLineEdit, QTextEdit, QVBoxLayout)

from coinpilot_ai.trading.models import number


def edit_journal_note(store, scope, trade, parent):
    note = trade.get("note", {})
    dialog = QDialog(parent)
    dialog.setWindowTitle("交易日志 · " + trade["instrument"])
    dialog.resize(470, 370)
    root = QVBoxLayout(dialog)
    form = QFormLayout()
    reason = QTextEdit(note.get("reason", ""))
    mistake = QTextEdit(note.get("mistake", ""))
    tags = QLineEdit(", ".join(note.get("tags", [])))
    risk = QLineEdit(str(note.get("risk_budget", "")))
    risk.setPlaceholderText("当时计划的最大亏损 USDT；留空则不计算 R")
    for title, widget in (("交易理由", reason), ("错误与改进", mistake),
                          ("标签（逗号分隔）", tags), ("初始风险 USDT", risk)):
        form.addRow(title, widget)
    root.addLayout(form)
    feedback = QLabel()
    root.addWidget(feedback)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                               QDialogButtonBox.StandardButton.Cancel)
    root.addWidget(buttons)
    buttons.rejected.connect(dialog.reject)

    def save():
        try:
            if risk.text().strip():
                number(risk.text(), "初始风险", positive=True)
            store.put("journal_note", trade["id"], {"version": 1,
                "reason": reason.toPlainText().strip(), "mistake": mistake.toPlainText().strip(),
                "tags": [value.strip() for value in tags.text().split(",") if value.strip()],
                "risk_budget": risk.text().strip()}, scope)
            dialog.accept()
        except ValueError as exc:
            feedback.setText(str(exc))
    buttons.accepted.connect(save)
    return dialog.exec() == QDialog.DialogCode.Accepted
