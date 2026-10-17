"""交易图表快照契约：不可变采集证据，不是可执行的图表恢复指令。"""
from dataclasses import dataclass
import json
import re
import time

from coinpilot_ai.market.intervals import BARS

MAX_SNAPSHOTS = 32


@dataclass(frozen=True)
class ChartSnapshot:
    id: str
    image_path: str
    sha256: str
    captured_at: int
    instrument: str
    period: str
    display_period: str
    stage: str
    note: str
    state_json: str

    @classmethod
    def parse(cls, raw: object) -> "ChartSnapshot":
        if not isinstance(raw, dict):
            raise ValueError("图表快照格式无效")
        values: dict[str, str] = {}
        for key in ("id", "image_path", "sha256", "instrument", "period", "display_period", "stage", "note", "state_json"):
            value = raw.get(key)
            if not isinstance(value, str):
                raise ValueError("图表快照字段缺失或格式无效")
            values[key] = value
        captured = raw.get("captured_at")
        if not isinstance(captured, int) or isinstance(captured, bool) or not 0 < captured <= int(time.time() * 1000) + 1000:
            raise ValueError("快照采集时间无效")
        if not re.fullmatch(r"[0-9a-f]{32}", values["id"]) or not re.fullmatch(r"[0-9a-f]{64}", values["sha256"]):
            raise ValueError("快照标识或校验摘要无效")
        if not re.fullmatch(r"[A-Z0-9]+-USDT-SWAP", values["instrument"]):
            raise ValueError("快照图表品种无效")
        if values["period"] not in BARS or values["display_period"] not in BARS:
            raise ValueError("快照周期无效")
        if values["stage"] not in ("分析", "开仓", "持仓", "平仓", "复盘") or len(values["note"]) > 2000:
            raise ValueError("快照阶段或备注无效")
        if len(values["state_json"].encode()) > 512 * 1024:
            raise ValueError("快照配置过大，请减少图表对象后重试")
        state = json.loads(values["state_json"])
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError("快照配置版本无效")
        json.dumps(state, allow_nan=False)
        return cls(values["id"], values["image_path"], values["sha256"], captured, values["instrument"],
                   values["period"], values["display_period"], values["stage"], values["note"], values["state_json"])

    def summary(self) -> dict[str, object]:
        return {"id": self.id, "captured_at": self.captured_at, "instrument": self.instrument,
                "period": self.period, "display_period": self.display_period, "stage": self.stage, "note": self.note,
                "image_access": "图片与完整画线/指标配置仅本地保存，AI 未读取图片；采集时间不等于交易时间"}
