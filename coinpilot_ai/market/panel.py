"""盘口、逐笔、衍生品快照与成交量分布。"""
import time
from datetime import datetime, timezone

from PyQt6.QtCore import QDateTime, QTimer
from PyQt6.QtWidgets import QComboBox, QDateTimeEdit, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .depth import MarketMicro
from .microstructure import volume_profile
from coinpilot_ai.ui.common import fill_table, table


class MicroPanel(QWidget):
    def __init__(self, service, chart, parent=None):
        super().__init__(parent)
        self.service, self.chart = service, chart
        self.feed = MarketMicro(service, self)
        root = QVBoxLayout(self)
        root.setContentsMargins(3, 3, 3, 3)
        self.status = QLabel("选中合约的公开盘口与逐笔成交")
        root.addWidget(self.status)
        self.derivatives = QLabel()
        self.derivatives.setWordWrap(True)
        root.addWidget(self.derivatives)
        self.book = table(["卖价", "卖量", "买价", "买量"], readable=True)
        self.book.setMaximumHeight(155)
        root.addWidget(self.book)
        self.cvd = QLabel()
        root.addWidget(self.cvd)
        self.trades = table(["时间", "方向", "价格", "张数"], readable=True)
        self.trades.setMaximumHeight(155)
        root.addWidget(self.trades)
        controls = QHBoxLayout()
        self.profile_mode = QComboBox()
        for key, title in (("visible", "可见范围"), ("fixed", "固定范围"), ("session", "UTC 今日")):
            self.profile_mode.addItem(title, key)
        controls.addWidget(self.profile_mode)
        root.addLayout(controls)
        self.begin = QDateTimeEdit(QDateTime.currentDateTime().addSecs(-3600))
        self.end = QDateTimeEdit(QDateTime.currentDateTime())
        root.addWidget(self.begin)
        root.addWidget(self.end)
        self.profile = QLabel("等待逐笔成交数据")
        self.profile.setWordWrap(True)
        root.addWidget(self.profile)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(250)
        self.render_timer.timeout.connect(self.refresh)
        self.feed.changed.connect(self.render_timer.start)
        self.profile_mode.currentIndexChanged.connect(self.refresh)
        self.begin.dateTimeChanged.connect(self.refresh)
        self.end.dateTimeChanged.connect(self.refresh)

    def refresh(self, *_):
        feed, now = self.feed, time.time()
        stale = now-feed.book.time > 30
        self.status.setText(f"{feed.instrument or '—'} · {feed.status}" + (" · 盘口过期" if stale and feed.book.sequence else ""))
        labels = []
        for key, name in (("funding", "Funding"), ("oi", "OI"), ("mark", "Mark"),
                          ("index", "Index"), ("basis", "Basis %")):
            value = feed.derivatives.get(key)
            if value:
                stamp = datetime.fromtimestamp(value["time"]).strftime("%H:%M:%S")
                labels.append(f"{name}: {value['value']} · {stamp} · {'过期' if now-value['time'] > 120 else '实时'}")
            else:
                labels.append(f"{name}: 不可用")
        self.derivatives.setText("\n".join(labels))
        asks, bids = feed.book.top(10)
        fill_table(self.book, [(str(index), [str(asks[index][0]) if index < len(asks) else "",
                                           str(asks[index][1]) if index < len(asks) else "",
                                           str(bids[index][0]) if index < len(bids) else "",
                                           str(bids[index][1]) if index < len(bids) else ""])
                               for index in range(max(len(asks), len(bids)))])
        self.cvd.setText("CVD：逐笔缺失买卖方向，当前区间不可用" if not feed.tape.direction_available else
                         f"CVD（本次连接以来） {feed.tape.cvd}" if feed.tape.trades else
                         "CVD：等待有明确买卖方向的成交")
        fill_table(self.trades, [(row["id"], [datetime.fromtimestamp(row["time"]/1000).strftime("%H:%M:%S"),
                                                  row["side"], str(row["price"]), str(row["size"])])
                                 for row in list(feed.tape.trades)[-30:][::-1]])
        self.refresh_profile()

    def refresh_profile(self):
        mode = self.profile_mode.currentData()
        self.begin.setVisible(mode == "fixed")
        self.end.setVisible(mode == "fixed")
        if mode == "visible":
            canvas = self.chart.canvas
            begin = int(canvas.left_time or 0)
            end = int(begin+canvas.count*canvas.interval)
        elif mode == "fixed":
            begin, end = self.begin.dateTime().toMSecsSinceEpoch(), self.end.dateTime().toMSecsSinceEpoch()
        else:
            day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            begin, end = int(day.timestamp()*1000), int(time.time()*1000)
        rows = list(self.feed.tape.trades)
        if not rows or begin >= end:
            self.profile.setText("该范围尚无逐笔成交数据")
            return
        if rows[0]["time"] > begin or rows[-1]["time"] < end-30000:
            self.profile.setText("逐笔成交覆盖不足，暂不计算精确 Volume Profile")
            return
        spec = self.service.specs.get(self.feed.instrument, {})
        try:
            result = volume_profile(rows, spec["tickSz"], begin=begin, end=end)
        except (ValueError, KeyError):
            result = None
        self.profile.setText((f"POC {result['poc']} · VAH {result['vah']} · VAL {result['val']} · "
                              f"成交量 {result['total']}") if result else "该范围无成交")

    def showEvent(self, event):
        super().showEvent(event)
        self.feed.start()

    def hideEvent(self, event):
        self.feed.stop()
        super().hideEvent(event)
