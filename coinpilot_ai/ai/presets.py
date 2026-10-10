from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    id: str
    title: str
    instruction: str


PRESETS = (
    Preset("general", "通用助手", "根据当前工作区和用户问题，按需读取资料并说明依据。"),
    Preset("market", "行情分析", "分析多周期行情、指标与衍生品，说明新鲜度、预热和数据缺口。"),
    Preset("event", "事件解读", "解释固定提醒的事实、可能含义以及仍需确认的信息。"),
    Preset("order", "交易计划", "计算风险、数量和保护价格；仅创建可编辑订单草稿，不执行交易。"),
    Preset("review", "交易复盘", "基于程序计算的盈亏与已记录理由复盘，不补造当时未知的动机。"),
)
