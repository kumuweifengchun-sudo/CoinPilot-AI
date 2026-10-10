"""单根 K 线上下文，执行器不持有完整 Candle 数据的副本。"""
from dataclasses import dataclass

from coinpilot_ai.market.buffer import CandleRow


@dataclass(frozen=True)
class BarContext:
    row: CandleRow
    index: int

    def source(self, name: str) -> float | bool | int:
        row = self.row
        columns = {"open": 1, "high": 2, "low": 3, "close": 4, "volume": 5}
        if name in columns:
            return row[columns[name]]
        if name == "hl2":
            return (row[2] + row[3]) / 2
        if name == "hlc3":
            return (row[2] + row[3] + row[4]) / 3
        if name == "ohlc4":
            return (row[1] + row[2] + row[3] + row[4]) / 4
        if name == "barstate.isconfirmed":
            return row[8] == 1
        if name == "bar_index":
            return self.index
        raise ValueError("未知的行情输入字段")
