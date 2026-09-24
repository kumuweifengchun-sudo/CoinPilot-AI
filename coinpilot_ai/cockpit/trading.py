"""人工确认令牌、持久化提交状态与未知订单对账。"""
import copy
import time
import uuid

from .transport import ApiError


class TradingService:
    def __init__(self, api, store, scope, validate, changed, allowed=lambda: True):
        self.api, self.store, self.scope = api, store, scope
        self.validate, self.changed, self.allowed = validate, changed, allowed
        self.prepared = {}
        self.reconciling = set()
        self.closed = False

    def prepare(self, draft, context):
        if not self.allowed():
            raise ValueError("真实交易尚未解锁，请先完成模拟环境验收")
        for _, row in self.store.list("local_order", self.scope):
            if row["draft"]["instrument"] == draft.instrument and row["status"] in ("submitting", "unknown"):
                raise ValueError("该合约有结果待确认的提交，请先完成订单对账")
        client_id = "cw" + uuid.uuid4().hex[:26]
        payload = self.validate(draft, client_id)
        token = uuid.uuid4().hex
        self.prepared[token] = (copy.deepcopy(draft), payload, copy.deepcopy(context), time.time())
        return token, copy.deepcopy(payload)

    def submit(self, token, confirmed=False):
        if not confirmed:
            raise ValueError("订单必须由用户明确确认")
        if token not in self.prepared:
            raise ValueError("确认已使用或失效，请重新检查订单")
        draft, payload, context, prepared_at = self.prepared.pop(token)
        if self.closed or not self.allowed() or time.time() - prepared_at > 60:
            raise ValueError("确认已过期，请重新检查订单")
        fresh = self.validate(draft, payload["clOrdId"])
        if fresh != payload:
            raise ValueError("订单参数已经变化，请重新确认")
        client_id = payload["clOrdId"]
        row = {"client_id": client_id, "draft": draft.to_dict(), "payload": payload, "context": context,
               "status": "submitting", "time": time.time(), "protection": "待主订单成交后生成" if payload.get("attachAlgoOrds") else "未设置"}
        self.store.put("local_order", client_id, row, self.scope)
        self.changed(row)

        def done(rows, error):
            if self.closed:
                return
            current = self.store.get("local_order", client_id, row, self.scope)
            if error:
                current.update(status="unknown" if error.uncertain else "failed", error=str(error))
            elif not rows or str(rows[0].get("sCode", "0")) != "0":
                code = str(rows[0].get("sCode", "unknown")) if rows else "empty"
                current.update(status="unknown" if code in ("empty", "50004") else "failed", error="OKX 下单结果：" + code)
            else:
                current.update(status="live", order_id=rows[0].get("ordId", ""), error="")
            self.store.put("local_order", client_id, current, self.scope)
            self.changed(current)
            if current["status"] in ("unknown", "live"):
                self.reconcile(client_id)
        self.api.post("/api/v5/trade/order", payload, done)
        return client_id

    def reconcile(self, client_id):
        if self.closed or client_id in self.reconciling:
            return
        row = self.store.get("local_order", client_id, None, self.scope)
        if not row:
            return
        self.reconciling.add(client_id)

        def done(rows, error):
            self.reconciling.discard(client_id)
            if self.closed:
                return
            current = self.store.get("local_order", client_id, row, self.scope)
            if error or not rows:
                if current["status"] in ("submitting", "unknown"):
                    current["status"] = "unknown"
                    current["error"] = "结果待确认；未查到记录不等于下单失败。不会自动重发。"
            else:
                order = rows[0]
                current.update(status=order.get("state", "unknown"), order_id=order.get("ordId"), error="")
                self.store.put("orders", order["ordId"], order, self.scope)
                if current["payload"].get("attachAlgoOrds"):
                    attached = order.get("attachAlgoOrds") or []
                    failure = next((item for item in attached if item.get("failCode") not in (None, "", "0")), None)
                    if failure:
                        current["protection"] = "止盈止损生成失败：" + str(failure["failCode"])
                    elif attached and any(item.get("algoId") or item.get("attachAlgoId") for item in attached):
                        current["protection"] = "交易所已生成，详见止盈止损订单"
                    elif order.get("state") == "canceled":
                        current["protection"] = "主单已撤销，请核实已成交部分的保护状态"
                    else:
                        current["protection"] = "尚未确认生成，请检查止盈止损订单"
            self.store.put("local_order", client_id, current, self.scope)
            self.changed(current)
        self.api.get("/api/v5/trade/order", done, {"instId": row["draft"]["instrument"], "clOrdId": client_id}, private=True)

    def recover(self):
        for key, row in self.store.list("local_order", self.scope):
            if row["status"] in ("submitting", "unknown", "live", "partially_filled"):
                self.reconcile(key)

    def close(self):
        self.closed = True
        self.prepared.clear()
