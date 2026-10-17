"""多周期图表快照查看器：只显示已保存的图片，不重绘历史交易现场。"""
import hashlib
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea, QVBoxLayout, QWidget

from coinpilot_ai.ui.common import button, show_text
from .snapshots import ChartSnapshot


def snapshot_label(snapshot: ChartSnapshot) -> str:
    from datetime import datetime
    stamp = datetime.fromtimestamp(snapshot.captured_at / 1000).strftime("%Y-%m-%d %H:%M:%S")
    display = f"（显示 {snapshot.display_period}）" if snapshot.period != snapshot.display_period else ""
    return f"{snapshot.instrument} · {snapshot.period}{display} · {snapshot.stage}\n{stamp}" + (f" · {snapshot.note}" if snapshot.note else "")


class SnapshotGallery(QDialog):
    def __init__(self, snapshots: list[ChartSnapshot], parent: QWidget | None = None, *, selected: int = 0) -> None:
        super().__init__(parent)
        self.snapshots = list(snapshots)
        self.setWindowTitle("交易图表快照 · 采集时的固定画面")
        self.resize(1100, 760)
        root = QVBoxLayout(self)
        self.description = QLabel("可切换查看多个周期；图片采集时间与交易时间分别记录。")
        self.description.setWordWrap(True)
        root.addWidget(self.description)
        body = QHBoxLayout()
        self.items = QListWidget()
        self.items.setMinimumWidth(280)
        self.items.setMaximumWidth(360)
        self.items.setWordWrap(True)
        for snapshot in snapshots:
            item = QListWidgetItem(snapshot_label(snapshot))
            item.setToolTip(snapshot_label(snapshot))
            self.items.addItem(item)
        body.addWidget(self.items)
        self.area = QScrollArea()
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.area.setWidget(self.picture)
        body.addWidget(self.area, 1)
        root.addLayout(body, 1)
        self.original = QCheckBox("原始尺寸（可滚动查看）")
        root.addWidget(self.original)
        root.addWidget(button("查看保存的画线、指标与视口配置", self.show_state))
        self.items.currentRowChanged.connect(self.show_selected)
        self.original.toggled.connect(self.show_selected)
        self.items.setCurrentRow(min(max(0, selected), len(snapshots) - 1))

    def show_selected(self) -> None:
        index = self.items.currentRow()
        if not 0 <= index < len(self.snapshots):
            return
        snapshot = self.snapshots[index]
        self.description.setText(snapshot_label(snapshot) + "\n固定图片，不随当前图表变化；不表示程序已还原当时行情。")
        try:
            content = Path(snapshot.image_path).read_bytes()
            if hashlib.sha256(content).hexdigest() != snapshot.sha256:
                raise ValueError("快照校验失败，文件可能已被修改")
            pixmap = QPixmap()
            if not pixmap.loadFromData(content, "PNG"):
                raise ValueError("图片无法读取")
            if not self.original.isChecked():
                viewport = self.area.viewport()
                if viewport is not None:
                    pixmap = pixmap.scaled(viewport.size().expandedTo(self.size() / 2), Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
            self.picture.setPixmap(pixmap)
            self.picture.resize(pixmap.size() / pixmap.devicePixelRatio())
        except (OSError, ValueError) as exc:
            self.picture.clear()
            self.picture.setText(str(exc))
            self.picture.adjustSize()

    def show_state(self) -> None:
        index = self.items.currentRow()
        if 0 <= index < len(self.snapshots):
            import json
            show_text(self, "快照配置留档（只读，不自动恢复）",
                      json.dumps(json.loads(self.snapshots[index].state_json), ensure_ascii=False, indent=2))
