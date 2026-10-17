"""离线手动账本。只接收记录事实，不持有交易或网络服务。"""
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import time
import uuid
from typing import Literal, TypedDict

from coinpilot_ai.core.store import Store, encode
from coinpilot_ai.trading.models import number
from .statistics import Statistics
from .snapshots import ChartSnapshot, MAX_SNAPSHOTS

type Product = Literal["spot", "swap"]
type AmountMode = Literal["margin", "notional", "base", "all", "percent"]
ZERO = Decimal(0)


class ManualContext(TypedDict):
    manual_book_id: str
    book: dict[str, object]
    source: str
    trades: list[dict[str, object]]
    statistics: Statistics
    range: dict[str, int]
    boundary_note: str


def identity() -> str:
    return uuid.uuid4().hex


def obj(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or any(not isinstance(k, str) for k in raw):
        raise ValueError("记账记录必须是对象")
    return {k: v for k, v in raw.items()}


def text(row: dict[str, object], key: str, default: str = "") -> str:
    value = row.get(key, default)
    if not isinstance(value, str) or len(value) > 20000:
        raise ValueError(f"{key} 格式无效或过长")
    return value


def integer(row: dict[str, object], key: str, default: int = 0) -> int:
    value = row.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} 必须是整数")
    return value


def records(row: dict[str, object], key: str) -> list[dict[str, object]]:
    value = row.get(key, [])
    if not isinstance(value, list) or len(value) > 5000:
        raise ValueError(f"{key} 数量或格式无效")
    return [obj(item) for item in value]


def stamp(value: int) -> None:
    if value <= 0 or value > int(time.time() * 1000) + 1000:
        raise ValueError("实际记录时间必须有效且不能在未来")


@dataclass
class ManualFill:
    id: str = field(default_factory=identity)
    time: int = 0
    action: Literal["open", "close"] = "open"
    price: str = ""
    amount_mode: AmountMode = "notional"
    amount: str = ""
    leverage: str = ""
    reason: str = ""
    external_id: str = ""
    quantity: str = ""  # 经校验计算保存，读取时重新计算而不信任该缓存。

    @classmethod
    def parse(cls, raw: object) -> "ManualFill":
        row = obj(raw)
        action, mode = text(row, "action"), text(row, "amount_mode")
        if action not in ("open", "close") or mode not in ("margin", "notional", "base", "all", "percent"):
            raise ValueError("成交操作或数量方式无效")
        return cls(text(row, "id"), integer(row, "time"), action, text(row, "price"), mode,
                   text(row, "amount"), text(row, "leverage"), text(row, "reason"),
                   text(row, "external_id"), text(row, "quantity"))


@dataclass
class ManualCost:
    kind: Literal["fee", "funding"] = "fee"
    amount: str = ""  # 手续费正数为支出，负数为返佣；资金费正数为收入。
    currency: str = "USDT"
    usdt: str = ""
    fill_id: str = ""

    @classmethod
    def parse(cls, raw: object) -> "ManualCost":
        row = obj(raw)
        kind = text(row, "kind")
        if kind not in ("fee", "funding"):
            raise ValueError("费用类型无效")
        return cls(kind, text(row, "amount"), text(row, "currency"), text(row, "usdt"), text(row, "fill_id"))


@dataclass
class EquitySnapshot:
    id: str = field(default_factory=identity)
    time: int = 0
    equity: str = ""
    source: str = "manual"

    @classmethod
    def parse(cls, raw: object) -> "EquitySnapshot":
        row = obj(raw)
        return cls(text(row, "id"), integer(row, "time"), text(row, "equity"))


@dataclass
class Cashflow:
    time: int
    amount: str
    memo: str = ""
    source: str = "manual"


@dataclass
class ManualTrade:
    id: str = field(default_factory=identity)
    book_id: str = ""
    product: Product = "swap"
    symbol: str = "BTC-USDT"
    direction: Literal["long", "short"] = "long"
    mode: Literal["summary", "detail"] = "summary"
    fills: list[ManualFill] = field(default_factory=list)
    costs: list[ManualCost] = field(default_factory=list)
    fees_known: bool = False
    funding_known: bool = False
    before: EquitySnapshot | None = None
    after: EquitySnapshot | None = None
    notes: dict[str, str] = field(default_factory=dict)
    attachments: list[str] = field(default_factory=list)
    chart_snapshots: list[ChartSnapshot] = field(default_factory=list)
    version: int = 0
    created_at: int = 0
    updated_at: int = 0
    voided: bool = False

    @classmethod
    def parse(cls, raw: object) -> "ManualTrade":
        row = obj(raw)
        product, direction, mode = text(row, "product"), text(row, "direction"), text(row, "mode")
        if product not in ("spot", "swap") or direction not in ("long", "short") or mode not in ("summary", "detail"):
            raise ValueError("交易产品、方向或录入方式无效")
        for key in ("fees_known", "funding_known", "voided"):
            if not isinstance(row.get(key, False), bool):
                raise ValueError("费用状态无效")
        notes = obj(row.get("notes", {}))
        attachments = row.get("attachments", [])
        if not isinstance(attachments, list) or any(not isinstance(v, str) for v in attachments):
            raise ValueError("附件格式无效")
        return cls(id=text(row, "id"), book_id=text(row, "book_id"), product=product,
                   symbol=text(row, "symbol"), direction=direction, mode=mode,
                   fills=[ManualFill.parse(v) for v in records(row, "fills")],
                   costs=[ManualCost.parse(v) for v in records(row, "costs")],
                   fees_known=row.get("fees_known") is True, funding_known=row.get("funding_known") is True,
                   before=EquitySnapshot.parse(row["before"]) if row.get("before") is not None else None,
                   after=EquitySnapshot.parse(row["after"]) if row.get("after") is not None else None,
                   notes={key: text(notes, key) for key in notes}, attachments=list(attachments),
                   chart_snapshots=[ChartSnapshot.parse(v) for v in records(row, "chart_snapshots")],
                   version=integer(row, "version"), created_at=integer(row, "created_at"),
                   updated_at=integer(row, "updated_at"), voided=row.get("voided") is True)


@dataclass
class Calculation:
    quantity: Decimal | None
    opened: Decimal
    closed: Decimal
    realized: Decimal | None
    fees: Decimal
    funding: Decimal
    net: Decimal | None
    start: int
    end: int | None
    gaps: list[str]

    @property
    def status(self) -> str:
        return "incomplete" if self.gaps else "closed" if self.end is not None else "open"


def opening_quantity(price: str, mode: AmountMode, amount: str, leverage: str = "") -> Decimal:
    px, value = number(price, "成交价", positive=True), number(amount, "数量或金额", positive=True)
    if leverage:
        number(leverage, "杠杆", positive=True)
    if mode == "margin":
        return value * number(leverage, "杠杆", positive=True) / px
    if mode == "notional":
        return value / px
    if mode == "base":
        return value
    raise ValueError("开仓数量方式无效")


def calculate(trade: ManualTrade) -> Calculation:
    if not re.fullmatch(r"[A-Z0-9]+-USDT", trade.symbol):
        raise ValueError("交易对须为 USDT 计价，例如 BTC-USDT")
    if trade.product == "spot" and trade.direction != "long":
        raise ValueError("现货只支持非杠杆买入卖出")
    if not trade.fills or len(trade.fills) > 5000:
        raise ValueError("请记录开仓成交")
    if trade.mode == "summary" and ([f.action for f in trade.fills] not in (["open"], ["open", "close"])):
        raise ValueError("简录仅支持一次汇总开仓与最终平仓；分批操作请使用明细")
    ids = [f.id for f in trade.fills]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("成交编号缺失或重复")
    gaps: list[str] = []
    if not trade.fees_known:
        gaps.append("手续费尚未确认完整")
    if trade.product == "swap" and not trade.funding_known:
        gaps.append("资金费尚未确认完整")
    fees = funding = ZERO
    base_fees: dict[str, Decimal] = {}
    for cost in trade.costs:
        amount = number(cost.amount, "费用金额")
        if not cost.currency or cost.fill_id and cost.fill_id not in ids:
            raise ValueError("费用币种或关联成交无效")
        if cost.kind == "funding" and trade.product != "swap":
            raise ValueError("现货不能记录资金费")
        converted = amount if cost.currency == "USDT" else number(cost.usdt, "费用折算") if cost.usdt else None
        if converted is None:
            gaps.append("非 USDT 费用未折算")
        elif (amount > 0) != (converted > 0) or (amount < 0) != (converted < 0):
            raise ValueError("费用折算必须保持金额方向")
        if cost.kind == "funding":
            funding += converted or ZERO
        else:
            fees += converted or ZERO
            if trade.product == "spot" and cost.currency == trade.symbol.split("-")[0]:
                if not cost.fill_id:
                    raise ValueError("基础币手续费必须关联对应成交")
                base_fees[cost.fill_id] = base_fees.get(cost.fill_id, ZERO) + amount
    if trade.fills[0].action == "close":
        # 历史截断时只保留明确的平仓成交事实，不能假定开仓成本为零。
        closed = ZERO
        last = 0
        for fill in trade.fills:
            stamp(fill.time)
            if fill.action != "close" or fill.amount_mode != "base" or fill.time < last:
                raise ValueError("缺少开仓时请逐笔填写平仓币数量；补齐开仓后再归集完整过程")
            number(fill.price, "成交价", positive=True)
            size = number(fill.amount, "平仓数量", positive=True)
            fill.quantity = str(size)
            closed += size
            last = fill.time
        if trade.before or trade.after:
            raise ValueError("缺少完整开平仓边界，请先补齐成交再关联账户快照")
        gaps.append("缺少开仓记录，成本、剩余持仓和最终平仓时间未知")
        return Calculation(None, ZERO, closed, None, fees, funding, None, trade.fills[0].time, None, gaps)
    quantity = average = gross = opened = closed = ZERO
    last_time = 0
    end: int | None = None
    for fill in trade.fills:
        stamp(fill.time)
        if fill.time < last_time or end is not None:
            raise ValueError("成交须按时间排序；全部平仓后请新建交易")
        last_time = fill.time
        px = number(fill.price, "成交价", positive=True)
        base_fee = base_fees.get(fill.id, ZERO)
        if fill.action == "open":
            if trade.product == "spot" and (fill.amount_mode == "margin" or fill.leverage):
                raise ValueError("现货不使用保证金或杠杆")
            size = opening_quantity(fill.price, fill.amount_mode, fill.amount, fill.leverage)
            effective = size - base_fee
            if effective <= 0:
                raise ValueError("基础币手续费不能超过买入数量")
            # 基础币费用既减少持仓又计费用；毛盈亏补回其成本价值，避免重复扣减。
            gross -= base_fee * px
            average = (average * quantity + px * effective) / (quantity + effective)
            quantity += effective
            opened += effective
        else:
            if quantity <= 0:
                raise ValueError("缺少开仓或平仓数量超出剩余持仓")
            if fill.amount_mode == "all":
                size = quantity - base_fee
            elif fill.amount_mode == "percent":
                percent = number(fill.amount, "平仓比例", positive=True)
                if percent > 100:
                    raise ValueError("平仓比例不得大于 100%")
                size = quantity * percent / 100 - base_fee
            elif fill.amount_mode == "base":
                size = number(fill.amount, "平仓数量", positive=True)
            else:
                raise ValueError("平仓请选择全部、比例或基础币数量")
            consumed = size + base_fee
            if size <= 0 or consumed <= 0 or consumed > quantity:
                raise ValueError("平仓及基础币费用超出剩余持仓")
            gross += (px - average) * size * (1 if trade.direction == "long" else -1)
            gross -= base_fee * average
            quantity -= consumed
            closed += consumed
            if quantity == 0:
                end = fill.time
        fill.quantity = str(size)
    # 基础币扣费的现金流已进入毛盈亏，移出对应折算扣费以避免扣两次。
    for cost in trade.costs:
        if trade.product == "spot" and cost.kind == "fee" and cost.currency == trade.symbol.split("-")[0]:
            if cost.usdt:
                gross += number(cost.usdt)
    if trade.mode == "summary" and len(trade.fills) == 2 and quantity:
        raise ValueError("简录平仓必须关闭全部数量，分批平仓请使用明细")
    for snapshot in (trade.before, trade.after):
        if snapshot:
            stamp(snapshot.time)
            if number(snapshot.equity, "账户总权益") < 0:
                raise ValueError("账户总权益不能为负")
    if trade.before and trade.before.time > trade.fills[0].time:
        raise ValueError("开仓前账户快照时间不得晚于首次开仓")
    if trade.after and (end is None or trade.after.time < end):
        raise ValueError("平仓后账户快照须在最终平仓之后")
    for key in ("risk_budget", "planned_entry", "initial_stop", "target"):
        if trade.notes.get(key):
            number(trade.notes[key], key, positive=True)
    return Calculation(quantity, opened, closed, gross, fees, funding,
                       None if gaps else gross - fees + funding, trade.fills[0].time, end, list(dict.fromkeys(gaps)))


def journal_row(trade: ManualTrade) -> dict[str, object]:
    result = calculate(trade)
    record = asdict(trade)
    record["chart_snapshots"] = [snapshot.summary() for snapshot in trade.chart_snapshots]
    return {"id": trade.id, "book_id": trade.book_id, "source": "manual", "product": trade.product,
            "instrument": trade.symbol, "direction": trade.direction, "start": result.start, "end": result.end,
            "status": result.status, "quantity": str(result.quantity) if result.quantity is not None else None, "opened": str(result.opened),
            "closed": str(result.closed), "realized": str(result.realized) if result.realized is not None else None, "fees": str(-result.fees),
            "funding": str(result.funding), "net": str(result.net) if result.net is not None else None,
            "gaps": result.gaps, "note": dict(trade.notes, tags=[s.strip() for s in trade.notes.get("tags", "").split(",") if s.strip()]),
            "duration_ms": (result.end - result.start) if result.end else None,
            "record": record, "boundary_note": "手动记录；计划和理由可能事后补写，以录入和修改时间为准"}


class ManualLedger:
    """账本的持久化接口。调用者无需配置账户；不修改任何交易所记录。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def books(self) -> list[dict[str, str]]:
        return [{"id": key, "name": text(obj(raw), "name"), "exchange": text(obj(raw), "exchange"),
                 "account": text(obj(raw), "account")} for key, raw in self.store.list("manual_book")]

    def create_book(self, name: str, exchange: str, account: str) -> str:
        if not all(s.strip() and len(s) <= 100 for s in (name, exchange, account)):
            raise ValueError("请填写账本名称、交易所和账户别名（各不超过 100 字）")
        if any(b["exchange"].casefold() == exchange.strip().casefold() and b["account"] == account.strip() for b in self.books()):
            raise ValueError("该交易所与账户别名已有账本，请使用已有账本")
        key = identity()
        self.store.put("manual_book", key, {"name": name.strip(), "exchange": exchange.strip(), "account": account.strip()})
        return key

    def scope(self, book_id: str) -> str:
        if not self.store.get("manual_book", book_id):
            raise ValueError("手动账本不存在")
        return "manual:" + book_id

    def get(self, book_id: str, trade_id: str) -> ManualTrade:
        raw = self.store.get("manual_trade", trade_id, scope=self.scope(book_id))
        if raw is None:
            raise ValueError("该账本中没有这笔交易")
        return ManualTrade.parse(raw)

    def trades(self, book_id: str, *, include_voided: bool = False) -> list[ManualTrade]:
        values = [ManualTrade.parse(row) for _, row in self.store.list("manual_trade", self.scope(book_id))]
        return sorted((t for t in values if include_voided or not t.voided), key=lambda t: (t.fills[0].time, t.id))

    def similar(self, trade: ManualTrade) -> bool:
        first = trade.fills[0]
        return any(t.id != trade.id and t.symbol == trade.symbol and t.direction == trade.direction
                   and t.fills[0].time == first.time and number(t.fills[0].price) == number(first.price)
                   for t in self.trades(trade.book_id))

    def save(self, trade: ManualTrade, *, allow_similar: bool = False, allow_conversion: bool = False) -> ManualTrade:
        # 通过与磁盘相同的边界验证，避免调用者传入非法 Literal 等值。
        trade = ManualTrade.parse(asdict(trade))
        if not re.fullmatch(r"[0-9a-f]{32}", trade.id):
            raise ValueError("交易编号无效")
        scope = self.scope(trade.book_id)
        calculate(trade)
        prior_raw = self.store.get("manual_trade", trade.id, scope=scope)
        prior = ManualTrade.parse(prior_raw) if prior_raw else None
        if prior is None and trade.version:
            raise ValueError("原记录不在该账本，不能移动已保存交易")
        if prior and prior.version != trade.version:
            raise ValueError("记录已被修改，请重新打开后编辑")
        if prior and prior.mode != trade.mode:
            if prior.mode != "summary" or trade.mode != "detail" or not allow_conversion:
                raise ValueError("请补齐明细、核对汇总并确认转换；明细不能转回简录")
            old, new = calculate(prior), calculate(trade)
            if (old.opened, old.closed, old.quantity, old.realized, old.fees, old.funding, old.net) != (
                    new.opened, new.closed, new.quantity, new.realized, new.fees, new.funding, new.net):
                raise ValueError("明细的数量、毛盈亏或费用与原简录不一致，请先核对")
        external = [f.external_id for f in trade.fills if f.external_id]
        if len(external) != len(set(external)):
            raise ValueError("外部成交号重复")
        for other in self.trades(trade.book_id):
            if other.id != trade.id and set(external) & {f.external_id for f in other.fills if f.external_id}:
                raise ValueError("此外部成交号已记账")
        if not allow_similar and self.similar(trade):
            raise ValueError("存在同品种、方向、开仓时间及价格的相似记录，请核对后确认")
        if len(trade.chart_snapshots) > MAX_SNAPSHOTS or len({s.id for s in trade.chart_snapshots}) != len(trade.chart_snapshots):
            raise ValueError(f"每笔最多保存 {MAX_SNAPSHOTS} 张图表快照，且编号不能重复")
        existing_snapshots = {s.id: s for s in prior.chart_snapshots} if prior else {}
        for snapshot in trade.chart_snapshots:
            old = existing_snapshots.get(snapshot.id)
            if old and replace(snapshot, image_path=old.image_path) != old:
                raise ValueError("已保存快照不能修改；请新增快照，原版本会保留")
        trade.version = (prior.version if prior else 0) + 1
        trade.created_at = prior.created_at if prior else int(time.time() * 1000)
        trade.updated_at = int(time.time() * 1000)
        writes: list[tuple[str, str, object]] = []
        if prior:
            writes.append(("manual_revision", f"{trade.id}:{prior.version}", asdict(prior)))
        # 快照使用账户时间作为唯一键，重叠交易只引用同一份账户快照。
        for snapshot in (trade.before, trade.after):
            if snapshot:
                snapshot.id = str(snapshot.time)
                existing = self.store.get("manual_equity", snapshot.id, scope=scope)
                if existing and number(obj(existing)["equity"]) != number(snapshot.equity):
                    raise ValueError("同一账户时间已有不同权益快照，请核对时间或金额")
                writes.append(("manual_equity", snapshot.id, asdict(snapshot)))
        managed: list[str] = []
        for attachment in trade.attachments:
            source = Path(attachment).resolve()
            if not source.is_file() or source.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or source.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("截图须为有效图片文件且不超过 20 MiB")
            target = self.store.path.with_suffix(".manual-attachments") / trade.book_id / trade.id
            target.mkdir(parents=True, exist_ok=True)
            target = target / (hashlib.sha256(source.read_bytes()).hexdigest() + source.suffix.lower())
            if source != target.resolve() and not target.exists():
                shutil.copyfile(source, target)
            managed.append(str(target.resolve()))
        trade.attachments = managed
        snapshots: list[ChartSnapshot] = []
        for snapshot in trade.chart_snapshots:
            source = Path(snapshot.image_path).resolve()
            if not source.is_file() or source.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("图表快照文件缺失或超过 20 MiB")
            content = source.read_bytes()
            if not content.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(content).hexdigest() != snapshot.sha256:
                raise ValueError("图表快照校验失败，请重新采集")
            target = self.store.path.with_suffix(".manual-attachments") / trade.book_id / trade.id / "charts"
            target.mkdir(parents=True, exist_ok=True)
            target = target / (snapshot.sha256 + ".png")
            if not target.exists():
                target.write_bytes(content)
            elif hashlib.sha256(target.read_bytes()).hexdigest() != snapshot.sha256:
                raise ValueError("已保存快照文件损坏，不能覆盖原始证据")
            snapshots.append(replace(snapshot, image_path=str(target.resolve())))
        trade.chart_snapshots = snapshots
        result = calculate(trade)
        writes.append(("manual_trade", trade.id, dict(asdict(trade), review_time=result.end or result.start)))
        self.store.put_many(writes, scope)
        return trade

    def void(self, book_id: str, trade_id: str) -> None:
        trade = self.get(book_id, trade_id)
        trade.voided = True
        self.save(trade, allow_similar=True)

    def add_cashflow(self, book_id: str, when: int, amount: str, memo: str = "") -> str:
        stamp(when)
        value = number(amount, "转入正数／转出负数")
        if not value:
            raise ValueError("资金流水金额不能为零")
        return self.store.append("manual_cashflow", asdict(Cashflow(when, str(value), memo)), self.scope(book_id))

    def void_cashflow(self, book_id: str, key: str) -> None:
        scope = self.scope(book_id)
        with self.store.db:
            self.store.db.execute("UPDATE records SET kind='manual_cashflow_void' WHERE scope=? AND kind='manual_cashflow' AND id=?", (scope, key))

    def correct_snapshot(self, book_id: str, when: int, equity: str) -> None:
        scope = self.scope(book_id)
        previous = self.store.get("manual_equity", str(when), scope=scope)
        if previous is None or number(equity, "账户权益") < 0:
            raise ValueError("快照不存在或权益金额无效")
        snapshot = EquitySnapshot(str(when), when, str(number(equity)))
        writes: list[tuple[str, str, object]] = [
            ("manual_equity_revision", str(when) + ":" + identity(), previous),
            ("manual_equity", str(when), asdict(snapshot))]
        for trade in self.trades(book_id, include_voided=True):
            if not any(s and s.time == when for s in (trade.before, trade.after)):
                continue
            writes.append(("manual_revision", f"{trade.id}:{trade.version}", asdict(trade)))
            if trade.before and trade.before.time == when:
                trade.before = snapshot
            if trade.after and trade.after.time == when:
                trade.after = snapshot
            trade.version += 1
            trade.updated_at = int(time.time() * 1000)
            result = calculate(trade)
            writes.append(("manual_trade", trade.id, dict(asdict(trade), review_time=result.end or result.start)))
        self.store.put_many(writes, scope)

    def account_change(self, book_id: str, begin: int, end: int, *, complete: bool = False) -> dict[str, object]:
        scope = self.scope(book_id)
        before = self.store.get("manual_equity", str(begin), scope=scope)
        after = self.store.get("manual_equity", str(end), scope=scope)
        if not before or not after or begin >= end:
            return {"change": None, "adjusted": None, "note": "缺少账户权益快照"}
        change = number(obj(after)["equity"]) - number(obj(before)["equity"])
        flows = [obj(row) for _, row in self.store.list("manual_cashflow", scope)]
        net_deposit = sum((number(r["amount"]) for r in flows if begin < integer(r, "time") <= end), ZERO)
        return {"change": str(change), "net_deposit": str(net_deposit),
                "adjusted": str(change - net_deposit) if complete else None,
                "note": "账户权益变化包含其他交易与浮盈亏，不等同于本笔盈亏或账户收益率"}

    def review(self, book_id: str, begin: int, end: int, ids: list[str] | None = None) -> ManualContext:
        return read_review(self.store.db, book_id, begin, end, ids)


def read_review(db: sqlite3.Connection, book_id: str, begin: int, end: int,
                ids: list[str] | None = None, *, limit: int = 5000, instrument: str | None = None) -> ManualContext:
    """UI 与 AI 共用有界查询，先按账本和整笔交易的统计时间筛选。"""
    from .statistics import statistics
    book = db.execute("SELECT body FROM records WHERE scope='global' AND kind='manual_book' AND id=?", (book_id,)).fetchone()
    if not book or not 1 <= limit <= 5000 or begin > end or ids and len(ids) > limit:
        raise ValueError("账本或查询范围无效")
    sql = "SELECT body FROM records WHERE scope=? AND kind='manual_trade' AND json_valid(body) AND json_extract(body,'$.voided')=0"
    args: list[object] = ["manual:" + book_id]
    if ids:
        sql += " AND id IN (" + ",".join("?" for _ in ids) + ")"
        args.extend(ids)
    else:
        sql += " AND json_extract(body,'$.review_time') BETWEEN ? AND ?"
        args.extend((begin, end))
    if instrument:
        sql += " AND json_extract(body,'$.symbol')=?"
        args.append(instrument)
    sql += " ORDER BY json_extract(body,'$.review_time'),id LIMIT ?"
    args.append(limit + 1)
    rows: list[dict[str, object]] = []
    size = 0
    for raw, in db.execute(sql, args):
        size += len(raw.encode())
        if len(rows) >= limit or size > 8 * 1024 * 1024:
            raise ValueError("复盘记录过多，请缩小范围")
        trade = ManualTrade.parse(json.loads(raw))
        row = journal_row(trade)
        rows.append(row)
    if ids and {str(r["id"]) for r in rows} != set(ids):
        raise ValueError("选择包含不存在或已作废的交易")
    return {"manual_book_id": book_id, "book": obj(json.loads(book[0])), "source": "manual", "trades": rows,
            "statistics": statistics(rows), "range": {"begin": begin, "end": end},
            "boundary_note": "手动事实可能事后补录；未知费用排除胜率；账户权益变化不能代替交易盈亏"}


def reference(book_id: str, key: str) -> str:
    return f"manual:{book_id}:{key}"


def parse_reference(value: str) -> tuple[str, str] | None:
    if not value.startswith("manual:"):
        return None
    parts = value.split(":")
    if len(parts) != 3 or not all(re.fullmatch(r"[0-9a-f]{32}", p) for p in parts[1:]):
        raise ValueError("手动账本引用无效")
    return parts[1], parts[2]
