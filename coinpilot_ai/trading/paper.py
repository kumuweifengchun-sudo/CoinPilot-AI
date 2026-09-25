"""本地虚拟资金撮合。私有操作绝不转发网络；公共行情仍由 OKX 提供。"""
from copy import deepcopy
from decimal import Decimal
import time
import sqlite3

from .models import aligned, number
from .matching import execution_fee, execution_price, protective_exit
from coinpilot_ai.integrations.transport import ApiError


class PaperBroker:
    SCOPE = 'paper:local'
    INITIAL = '100000'
    FEE = Decimal('0.0005')
    credentials = {}

    def __init__(self, store, public_api, specs, quotes, changed, *, scope=None, clock=None,
                 fee=None, slippage_bps=0):
        self.SCOPE = scope or type(self).SCOPE
        self.clock = clock or time.time
        self.FEE = number(fee) if fee is not None else type(self).FEE
        self.slippage_bps = number(slippage_bps)
        if not 0 <= self.FEE <= 1 or not 0 <= self.slippage_bps < 10000:
            raise ValueError('手续费率须在 0—1，滑点须在 0—10000 基点以内')
        self.store, self.public_api = store, public_api
        self.specs, self.quotes, self.changed = specs, quotes, changed
        self.order_changes = []
        self.state = store.get('paper_account', 'main', None, self.SCOPE) or {
            'version': 1, 'cash': self.INITIAL, 'initial': self.INITIAL, 'sequence': 0,
            'positions': {}, 'pending': {}, 'algos': {}, 'leverages': {}}
        self.state.setdefault('version', 1)

    def _id(self):
        self.state['sequence'] += 1
        return str(self.state['sequence'])

    @staticmethod
    def _key(inst, margin):
        return inst + '|' + margin

    def leverage(self, inst, margin):
        return self.state['leverages'].get(self._key(inst, margin), '3')

    def _quote(self, inst):
        q = self.quotes().get(inst)
        if not q or q.get('source') != 'okx' or not -5 <= self.clock() - q['time'] <= 30:
            raise ValueError('实时行情未就绪或已过期，本地模拟暂停成交')
        return number(q['price'], positive=True)

    def _spec(self, inst):
        spec = self.specs().get(inst, {})
        if spec.get('state') != 'live' or spec.get('ctType') != 'linear' or spec.get('settleCcy') != 'USDT':
            raise ValueError('等待 USDT 永续合约规格加载')
        return spec

    def _unit(self, inst):
        spec = self._spec(inst)
        return number(spec['ctVal'], positive=True) * number(spec.get('ctMult') or '1', positive=True)

    def snapshot(self, exclude=None):
        positions, used, upl = [], Decimal(0), Decimal(0)
        for key, source in self.state['positions'].items():
            p = dict(source)
            qty, avg, unit = number(p['pos']), number(p['avgPx']), number(p['unit'])
            q = self.quotes().get(p['instId'])
            price = number(q['price']) if q else avg
            pnl = (price - avg) * qty * unit
            margin = abs(qty) * avg * unit / number(p['lever'])
            reserved = sum((number(o['sz']) for oid, o in self.state['pending'].items()
                            if oid != exclude and o.get('reduceOnly') and self._key(o['instId'], o['tdMode']) == key), Decimal(0))
            p.update(availPos=str(max(Decimal(0), abs(qty)-reserved)), upl=str(pnl),
                     uplRatio=str(pnl/margin if margin else 0))
            positions.append(p)
            used += margin
            upl += pnl
        frozen = sum((number(o['reserve']) for oid, o in self.state['pending'].items() if oid != exclude), Decimal(0))
        cash = number(self.state['cash'])
        return positions, {'totalEq': str(cash+upl), 'cashBal': str(cash), 'upl': str(upl),
                           'availEq': str(cash+upl-used-frozen), 'frozenBal': str(frozen), 'imr': str(used), 'ccy': 'USDT'}

    def validate(self, payload):
        inst, margin = payload['instId'], payload['tdMode']
        kind = payload.get('ordType')
        if kind not in ('market', 'limit', 'stop', 'stop_limit', 'trailing_stop'):
            raise ValueError('本地模拟订单类型无效')
        if payload.get('side') not in ('buy', 'sell') or margin not in ('cross', 'isolated'):
            raise ValueError('本地模拟方向或保证金模式无效')
        spec = self._spec(inst)
        qty = aligned(payload['sz'], spec['lotSz'], '合约张数')
        if qty < number(spec['minSz']):
            raise ValueError('数量小于合约最小张数')
        current = self._quote(inst)
        if kind in ('stop', 'stop_limit'):
            aligned(payload.get('triggerPx', ''), spec['tickSz'], '触发价')
        if kind in ('limit', 'stop_limit') and not payload.get('px'):
            raise ValueError('限价订单须填写价格')
        if kind == 'trailing_stop':
            if number(payload.get('trailOffset', ''), '追踪距离', positive=True) <= 0:
                raise ValueError('追踪距离须大于零')
        price = aligned(payload['px'], spec['tickSz'], '限价') if payload.get('px') else current
        entry = aligned(payload['triggerPx'], spec['tickSz'], '触发价') if kind == 'stop' else price
        for attached in payload.get('attachAlgoOrds', []):
            for prefix in ('sl', 'tp'):
                if not attached.get(prefix+'TriggerPx'):
                    continue
                level = aligned(attached[prefix+'TriggerPx'], spec['tickSz'], '保护触发价')
                above = (payload['side'] == 'buy') == (prefix == 'tp')
                if (above and level <= entry) or (not above and level >= entry):
                    raise ValueError('止盈止损方向与入场价格不匹配')
        positions, balance = self.snapshot()
        position = next((p for p in positions if p['instId'] == inst and p['mgnMode'] == margin), None)
        if payload.get('reduceOnly'):
            if not position or (number(position['pos']) > 0) != (payload['side'] == 'sell') or qty > number(position['availPos']):
                raise ValueError('平仓张数超过当前可平仓数量')
        else:
            if position and (number(position['pos']) > 0) != (payload['side'] == 'buy'):
                raise ValueError('有反向持仓，请先平仓')
            needed = qty * price * self._unit(inst) * (1 / number(self.leverage(inst, margin)) + self.FEE)
            if needed > number(balance['availEq']):
                raise ValueError('本地模拟可用资金不足（含挂单冻结资金与手续费）')

    def _transaction(self, operation):
        before = deepcopy(self.state)
        writes = []
        try:
            result = operation(writes)
            if writes or before != self.state:
                self.store.put_many([('paper_account', 'main', self.state), *writes], self.SCOPE)
                self.order_changes.extend(deepcopy(row) for kind, _, row in writes if kind == 'orders')
        except Exception:
            self.state = before
            raise
        return result

    def _save_order(self, order, writes):
        writes.append(('orders', order['ordId'], deepcopy(order)))
        local = self.store.get('local_order', order.get('clOrdId', ''), None, self.SCOPE)
        if local:
            local.update(status=order['state'], order_id=order['ordId'], error=order.get('error', ''))
            if order.get('attachAlgoOrds') and order['state'] == 'filled':
                local['protection'] = '本地止盈止损已生效'
            writes.append(('local_order', order['clOrdId'], local))

    def _fill(self, order, price, writes):
        inst, margin = order['instId'], order['tdMode']
        price = execution_price(price, order['side'], order.get('ordType'), self.slippage_bps)
        key = self._key(inst, margin)
        qty, unit = number(order['sz']), number(order['unit'])
        position = self.state['positions'].get(key)
        fee, pnl = execution_fee(price, qty, unit, self.FEE), Decimal(0)
        if order.get('reduceOnly'):
            if not position or qty > abs(number(position['pos'])) or (number(position['pos']) > 0) != (order['side'] == 'sell'):
                order.update(state='failed', error='持仓已变化，未执行超量平仓')
            else:
                old = number(position['pos'])
                pnl = (price-number(position['avgPx'])) * qty*unit*(1 if old > 0 else -1)
                left = abs(old)-qty
                if left:
                    position['pos'] = str(left if old > 0 else -left)
                else:
                    del self.state['positions'][key]
                    for aid, algo in list(self.state['algos'].items()):
                        if self._key(algo['instId'], algo['tdMode']) == key:
                            algo['state'] = 'canceled'
                            writes.append(('paper_algo', aid, deepcopy(algo)))
                            del self.state['algos'][aid]
        else:
            _, balance = self.snapshot(exclude=order['ordId'])
            required = qty*price*unit/number(order['lever'])+fee
            if position and (number(position['pos']) > 0) != (order['side'] == 'buy'):
                order.update(state='failed', error='反向持仓已存在，挂单未成交')
            elif required > number(balance['availEq']):
                order.update(state='failed', error='成交时可用虚拟资金不足')
            else:
                old = abs(number(position['pos'])) if position else Decimal(0)
                avg = (old*number(position['avgPx'])+qty*price)/(old+qty) if old else price
                self.state['positions'][key] = {'instId': inst, 'posId': 'paper:'+key, 'posSide': 'net',
                    'mgnMode': margin, 'pos': str((old+qty)*(1 if order['side'] == 'buy' else -1)),
                    'avgPx': str(avg), 'lever': order['lever'], 'unit': str(unit)}
        self.state['pending'].pop(order['ordId'], None)
        if order['state'] == 'failed':
            self._save_order(order, writes)
            return
        stamp = str(int(self.clock()*1000))
        self.state['cash'] = str(number(self.state['cash'])+pnl-fee)
        order.update(state='filled', accFillSz=str(qty), avgPx=str(price), uTime=stamp)
        fid = self._id()
        fill = {'billId': fid, 'tradeId': fid, 'ordId': order['ordId'], 'instId': inst,
                'side': order['side'], 'posSide': 'net', 'mgnMode': margin, 'fillSz': str(qty),
                'fillPx': str(price), 'fillPnl': str(pnl), 'fee': str(-fee), 'feeCcy': 'USDT',
                'fillTime': stamp, 'ts': stamp, 'source': 'paper'}
        writes.append(('fills', fid, fill))
        if not order.get('reduceOnly'):
            for attached in order.get('attachAlgoOrds', []):
                aid = self._id()
                algo = dict(attached, algoId=aid, algoClOrdId=attached.get('attachAlgoClOrdId', ''),
                            instId=inst, tdMode=margin, side='sell' if order['side'] == 'buy' else 'buy',
                            sz=str(qty), state='live', reduceOnly=True, ordType='oco', source='paper')
                self.state['algos'][aid] = algo
                writes.append(('paper_algo', aid, deepcopy(algo)))
                attached['algoId'] = aid
        self._save_order(order, writes)
        _, balance = self.snapshot()
        writes.append(('paper_equity', fid, {'time': stamp, 'cash': balance['cashBal'],
                                           'equity': balance['totalEq']}))

    def _order(self, payload, writes):
        prior = next((o for _, o in self.store.list('orders', self.SCOPE)
                      if payload.get('clOrdId') and o.get('clOrdId') == payload['clOrdId']), None)
        if prior:
            return [{'ordId': prior['ordId'], 'sCode': '0'}]
        self.validate(payload)
        inst = payload['instId']
        price, unit = self._quote(inst), self._unit(inst)
        oid = self._id()
        order = dict(deepcopy(payload), ordId=oid, state='live', accFillSz='0', source='paper',
                     uTime=str(int(self.clock()*1000)), unit=str(unit), lever=self.leverage(inst, payload['tdMode']))
        if order['ordType'] == 'trailing_stop':
            order['trailAnchor'] = str(price)
        limit = number(order.get('px') or price)
        order['reserve'] = str(Decimal(0) if order.get('reduceOnly') else number(order['sz'])*limit*unit*(1/number(order['lever'])+self.FEE))
        self.state['pending'][oid] = order
        if order['ordType'] == 'market' or (order['ordType'] == 'limit' and (price <= limit if order['side'] == 'buy' else price >= limit)):
            self._fill(order, price, writes)
        else:
            self._save_order(order, writes)
        return [{'ordId': oid, 'sCode': '0'}]

    def match(self, inst):
        try:
            price = self._quote(inst)
        except ValueError:
            return
        def operation(writes):
            for order in list(self.state['pending'].values()):
                if order['instId'] != inst:
                    continue
                kind, buy = order['ordType'], order['side'] == 'buy'
                if kind == 'trailing_stop':
                    previous_anchor = number(order['trailAnchor'])
                    anchor = min(previous_anchor, price) if buy else max(previous_anchor, price)
                    if anchor != previous_anchor:
                        order['trailAnchor'] = str(anchor)
                        writes.append(('orders', order['ordId'], deepcopy(order)))
                    triggered = price >= anchor+number(order['trailOffset']) if buy else price <= anchor-number(order['trailOffset'])
                    if triggered:
                        order['ordType'] = 'market'
                        self._fill(order, price, writes)
                elif kind in ('stop', 'stop_limit'):
                    trigger = number(order['triggerPx'])
                    triggered = price >= trigger if buy else price <= trigger
                    if triggered:
                        order['ordType'] = 'market' if kind == 'stop' else 'limit'
                        writes.append(('orders', order['ordId'], deepcopy(order)))
                        if order['ordType'] == 'market' or (price <= number(order['px']) if buy else price >= number(order['px'])):
                            self._fill(order, price, writes)
                elif kind == 'limit' and (price <= number(order['px']) if buy else price >= number(order['px'])):
                    self._fill(order, price, writes)
            for aid, algo in list(self.state['algos'].items()):
                if aid not in self.state['algos']:
                    continue
                if algo['instId'] != inst:
                    continue
                long = algo['side'] == 'sell'
                sl, tp = algo.get('slTriggerPx'), algo.get('tpTriggerPx')
                triggered = (sl and (price <= number(sl) if long else price >= number(sl))) or (tp and (price >= number(tp) if long else price <= number(tp)))
                if not triggered:
                    continue
                self.state['algos'].pop(aid)
                position = self.state['positions'].get(self._key(inst, algo['tdMode']))
                qty = min(number(algo['sz']), abs(number(position['pos']))) if position else Decimal(0)
                algo['state'] = 'effective' if qty else 'canceled'
                writes.append(('paper_algo', aid, deepcopy(algo)))
                if qty:
                    # 保护触发优先退出，撤销同仓位的普通平仓挂单后再成交。
                    for oid, pending in list(self.state['pending'].items()):
                        if pending.get('reduceOnly') and pending['instId'] == inst and pending['tdMode'] == algo['tdMode']:
                            pending.update(state='canceled', uTime=str(int(self.clock()*1000)))
                            self._save_order(pending, writes)
                            del self.state['pending'][oid]
                    order = dict(algo, ordId=self._id(), clOrdId='paper-protect-'+aid, ordType='market',
                                 sz=str(qty), state='live', unit=position['unit'], lever=position['lever'])
                    self._fill(order, price, writes)
            return bool(writes)
        if self._transaction(operation):
            self.changed()

    def match_bar(self, inst, row):
        """训练回放的 OHLC 撮合；无法判断同根先后时先处理止损。"""
        opened, high, low = (number(row[index]) for index in (1, 2, 3))
        def operation(writes):
            for order in list(self.state['pending'].values()):
                if order['instId'] != inst:
                    continue
                kind = order.get('ordType')
                buy = order['side'] == 'buy'
                if kind in ('stop', 'stop_limit'):
                    trigger = number(order['triggerPx'])
                    if (high >= trigger if buy else low <= trigger):
                        order['ordType'] = 'market' if kind == 'stop' else 'limit'
                        if kind == 'stop':
                            self._fill(order, max(opened, trigger) if buy else min(opened, trigger), writes)
                        else:
                            writes.append(('orders', order['ordId'], deepcopy(order)))
                    continue
                if kind == 'trailing_stop':
                    previous_anchor = number(order['trailAnchor'])
                    # 同根高低点缺少先后顺序：仅用此前锚点判断触发，再更新下一根的锚点。
                    trigger = (previous_anchor+number(order['trailOffset']) if buy else
                               previous_anchor-number(order['trailOffset']))
                    if (high >= trigger if buy else low <= trigger):
                        order['ordType'] = 'market'
                        self._fill(order, max(opened, trigger) if buy else min(opened, trigger), writes)
                    else:
                        anchor = min(previous_anchor, low) if buy else max(previous_anchor, high)
                        if anchor != previous_anchor:
                            order['trailAnchor'] = str(anchor)
                            writes.append(('orders', order['ordId'], deepcopy(order)))
                    continue
                if kind != 'limit':
                    continue
                limit = number(order['px'])
                if (low <= limit if buy else high >= limit):
                    self._fill(order, min(opened, limit) if buy else max(opened, limit), writes)
            for aid, algo in list(self.state['algos'].items()):
                if algo['instId'] != inst:
                    continue
                position = self.state['positions'].get(self._key(inst, algo['tdMode']))
                if not position:
                    continue
                long = number(position['pos']) > 0
                sl = number(algo['slTriggerPx']) if algo.get('slTriggerPx') else None
                tp = number(algo['tpTriggerPx']) if algo.get('tpTriggerPx') else None
                exit_plan = protective_exit(opened, high, low, sl, tp, long=long)
                if exit_plan is None:
                    continue
                price, cause, ambiguous = exit_plan
                self.state['algos'].pop(aid)
                algo['state'] = 'effective'
                algo['ambiguity'] = ambiguous
                writes.append(('paper_algo', aid, deepcopy(algo)))
                qty = min(number(algo['sz']), abs(number(position['pos'])))
                order = dict(algo, ordId=self._id(), clOrdId='replay-protect-'+aid,
                             ordType='market' if cause == 'stop' else 'limit',
                             sz=str(qty), state='live', unit=position['unit'], lever=position['lever'],
                             reduceOnly=True, px=str(price))
                self._fill(order, price, writes)
            return bool(writes)
        if self._transaction(operation):
            self.changed()

    def get(self, path, callback, params=None, private=False):
        if not private:
            return self.public_api.get(path, callback, params)
        params = params or {}
        try:
            positions, balance = self.snapshot()
            if path.endswith('/config'):
                rows = [{'posMode': 'net_mode', 'acctLv': '2'}]
            elif path.endswith('/balance'):
                rows = [balance]
            elif path.endswith('/positions'):
                rows = positions
            elif path.endswith('/leverage-info'):
                rows = [{'lever': self.leverage(params['instId'], params['mgnMode']), 'posSide': 'net'}]
            elif path.endswith('/orders-pending'):
                rows = list(self.state['pending'].values())
            elif path.endswith('/orders-algo-pending'):
                rows = list(self.state['algos'].values())
            elif path.endswith(('/order', '/order-algo')):
                kind = 'paper_algo' if path.endswith('/order-algo') else 'orders'
                rows = [r for _, r in self.store.list(kind, self.SCOPE) if all(str(r.get(k, '')) == str(v) for k, v in params.items())]
            elif path.endswith(('/fills-history', '/orders-history-archive', '/bills-archive', '/positions-history')):
                kind = 'fills' if path.endswith('/fills-history') else 'orders' if path.endswith('/orders-history-archive') else 'bills'
                rows = [r for _, r in self.store.list(kind, self.SCOPE)]
            else:
                raise ValueError('本地模拟不支持此账户查询')
            if not path.endswith(('/order', '/order-algo', '/config', '/balance', '/positions', '/leverage-info')):
                field = 'algoId' if 'algo' in path else 'billId' if 'fills' in path or 'bills' in path else 'ordId'
                rows = [r for r in rows if (not params.get('after') or int(r[field]) < int(params['after']))
                        and int(params.get('begin', 0)) <= int(r.get('fillTime') or r.get('uTime') or 0) <= int(params.get('end', 2**63-1))]
                rows = sorted(rows, key=lambda r: int(r[field]), reverse=True)[:int(params.get('limit', 100))]
        except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
            callback(None, ApiError(str(exc)))
            return
        callback(deepcopy(rows), None)

    def post(self, path, payload, callback):
        def operation(writes):
            if path.endswith('/order'):
                return self._order(payload, writes)
            if path.endswith('/set-leverage'):
                inst, margin = payload['instId'], payload['mgnMode']
                lever = number(payload['lever'], positive=True)
                if lever != int(lever) or lever > 125:
                    raise ValueError('杠杆必须为 1～125 的整数')
                if self._key(inst, margin) in self.state['positions'] or any(o['instId'] == inst and o['tdMode'] == margin for o in self.state['pending'].values()):
                    raise ValueError('本地模拟调整杠杆前请先平仓并撤销该模式挂单')
                self.state['leverages'][self._key(inst, margin)] = str(lever)
                return [{'lever': str(lever), 'sCode': '0'}]
            if path.endswith(('/cancel-order', '/cancel-algos')):
                algo = path.endswith('/cancel-algos')
                for request in payload if algo else [payload]:
                    key = request['algoId' if algo else 'ordId']
                    active = self.state['algos' if algo else 'pending']
                    row = active.get(key)
                    if not row or row['instId'] != request['instId']:
                        raise ValueError('未找到可撤销的本地订单')
                    row.update(state='canceled', uTime=str(int(self.clock()*1000)))
                    if algo:
                        writes.append(('paper_algo', key, deepcopy(row)))
                    else:
                        self._save_order(row, writes)
                    del active[key]
                return [{'sCode': '0'}]
            if path.endswith('/order-algo'):
                self.validate(dict(payload, ordType='market', reduceOnly=True))
                if not payload.get('slTriggerPx') and not payload.get('tpTriggerPx'):
                    raise ValueError('请填写止盈或止损')
                price = self._quote(payload['instId'])
                for prefix in ('sl', 'tp'):
                    if payload.get(prefix+'TriggerPx'):
                        level = aligned(payload[prefix+'TriggerPx'], self._spec(payload['instId'])['tickSz'], '触发价')
                        above = (payload['side'] == 'sell') == (prefix == 'tp')
                        if (above and level <= price) or (not above and level >= price):
                            raise ValueError('止盈止损方向与当前价格不匹配')
                aid = self._id()
                row = dict(deepcopy(payload), algoId=aid, state='live', source='paper')
                self.state['algos'][aid] = row
                writes.append(('paper_algo', aid, deepcopy(row)))
                return [{'algoId': aid, 'sCode': '0'}]
            raise ValueError('本地模拟不支持此操作')
        try:
            rows = self._transaction(operation)
        except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
            callback(None, ApiError(str(exc)))
            return
        callback(rows, None)
        self.changed()
