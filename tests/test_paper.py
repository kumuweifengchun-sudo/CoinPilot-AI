import time
from decimal import Decimal

import pytest

from coinpilot_ai.config import DEFAULT_CONFIG
from coinpilot_ai.cockpit.domain import OrderDraft
from coinpilot_ai.cockpit.paper import PaperBroker
from coinpilot_ai.cockpit.service import CockpitService
from coinpilot_ai.cockpit.store import Store
from coinpilot_ai.cockpit.workbench import Workbench

INST = 'BTC-USDT-SWAP'
SPEC = {'instId': INST, 'ctType': 'linear', 'settleCcy': 'USDT', 'state': 'live',
        'ctVal': '0.01', 'ctValCcy': 'BTC', 'lotSz': '0.01', 'minSz': '0.01', 'tickSz': '0.1'}


class NoVault:
    def read(self, key):
        raise AssertionError('本地模拟不得读取凭据')


@pytest.fixture
def paper(app, tmp_path, monkeypatch):
    store = Store(tmp_path/'paper.db')
    store.put('settings', 'workbench', {'environment': 'paper', 'watchlist': [INST], 'scenes': {}})
    store.close()
    service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), tmp_path/'paper.db', tmp_path/'icons',
                             vault=NoVault(), autostart=False)
    monkeypatch.setattr(service.transport, 'request', lambda *_a, **_k: pytest.fail('本地操作不得访问网络'))
    service.refresh_market = lambda: None
    service.fetch_candles = lambda *_: None
    service.specs = {INST: dict(SPEC)}
    quote(service, '60000')
    yield service
    service.close()
    service.deleteLater()
    app.processEvents()


def quote(service, price):
    service.market.exchange_times['BTCUSDT'] = time.time()
    service._price('BTCUSDT', Decimal(price), '', 'okx')


def submit(service, **kwargs):
    token, payload = service.trading.prepare(OrderDraft(INST, **kwargs), {})
    identity = service.trading.submit(token, confirmed=True)
    return service.store.get('local_order', identity, scope=service.scope)


def action(service, endpoint, payload):
    results = []
    service.trade_action('/api/v5/'+endpoint, payload, lambda rows, error: results.append((rows, error)))
    assert results and results[0][1] is None, results
    return results[0][0]


def test_market_open_partial_close_and_fees_are_persistent(paper):
    assert paper.balance['totalEq'] == '100000'
    row = submit(paper, size='2')
    assert row['status'] == 'filled'
    assert paper.positions[0]['pos'] == '2'
    assert Decimal(paper.balance['cashBal']) == Decimal('99999.4')
    quote(paper, '61000')
    assert Decimal(paper.balance['upl']) == 20
    submit(paper, action='close', size='1')
    assert Decimal(paper.positions[0]['pos']) == 1
    assert Decimal(paper.balance['cashBal']) == Decimal('100009.095')
    submit(paper, action='close', size='1')
    assert not paper.positions
    assert Decimal(paper.balance['cashBal']) == Decimal('100018.790')
    rows, _ = paper.journal()
    assert rows[0]['status'] == 'closed' and rows[0]['net'] == Decimal('18.790')
    balance = dict(paper.balance)
    paper.change_account('paper')
    assert paper.balance == balance
    assert len(paper.store.list('fills', paper.scope)) == 3
    assert not paper.store.list('demo_check')


def test_limit_fill_cancel_freeze_and_stale_prices(paper):
    row = submit(paper, order_type='limit', price='59000', size='1')
    assert row['status'] == 'live' and len(paper.pending_orders) == 1
    assert Decimal(paper.balance['frozenBal']) > 196
    paper.quotes[INST]['time'] = time.time()-60
    paper.quotes[INST]['price'] = '58000'
    paper.api.match(INST)
    assert not paper.positions
    quote(paper, '58000')
    assert paper.positions[0]['avgPx'] == '58000'
    assert Decimal(paper.balance['frozenBal']) == 0
    row = submit(paper, order_type='limit', price='57000', size='1')
    action(paper, 'trade/cancel-order', {'instId': INST, 'ordId': row['order_id']})
    assert not paper.pending_orders and Decimal(paper.balance['frozenBal']) == 0
    assert paper.store.get('local_order', row['client_id'], scope=paper.scope)['status'] == 'canceled'


@pytest.mark.parametrize('direction,trigger', [('long', '62000'), ('short', '58000')])
def test_attached_protection_and_short_positions(paper, direction, trigger):
    row = submit(paper, direction=direction, size='1', stop_loss='59000' if direction == 'long' else '61000',
                 take_profit=trigger)
    assert row['status'] == 'filled' and len(paper.algos) == 1
    quote(paper, trigger)
    assert not paper.positions and not paper.algos
    assert len(paper.store.list('fills', paper.scope)) == 2
    assert not paper.store.list('demo_check')


def test_reserve_margin_and_close_quantity_enforced(paper):
    with pytest.raises(ValueError, match='资金不足'):
        submit(paper, size='1000')
    submit(paper, size='3')
    submit(paper, action='close', order_type='limit', price='62000', size='2')
    assert Decimal(paper.positions[0]['availPos']) == 1
    with pytest.raises(ValueError, match='可平仓'):
        submit(paper, action='close', size='2')
    with pytest.raises(ValueError, match='整数倍'):
        submit(paper, size='0.001')
    assert len(paper.pending_orders) == 1


def test_protection_cancels_overlapping_close_and_orders_are_idempotent(paper):
    row = submit(paper, size='1', stop_loss='59000')
    pending = submit(paper, action='close', order_type='limit', price='62000', size='1')
    quote(paper, '58000')
    assert not paper.positions and not paper.pending_orders
    assert paper.store.get('local_order', pending['client_id'], scope=paper.scope)['status'] == 'canceled'
    results = []
    paper.api.post('/api/v5/trade/order', row['payload'], lambda *_: results.append(True))
    assert results and len(paper.store.list('fills', paper.scope)) == 2


def test_failure_rolls_back_wallet_and_fill_records(paper, monkeypatch):
    before = paper.api.snapshot()
    def fail(*_):
        raise OSError('disk full')
    monkeypatch.setattr(paper.store, 'put_many', fail)
    result = submit(paper, size='1')
    assert result['status'] == 'failed' and 'disk full' in result['error']
    assert paper.api.snapshot() == before
    assert not paper.store.list('fills', paper.scope)


def test_trigger_storage_failure_keeps_pending_order_and_recovers(paper, monkeypatch):
    row = submit(paper, order_type='limit', price='59000', size='1')
    save = paper.store.put_many
    def fail(*_):
        raise OSError('disk full')
    monkeypatch.setattr(paper.store, 'put_many', fail)
    quote(paper, '58000')
    assert not paper.positions and len(paper.pending_orders) == 1
    assert 'disk full' in paper.account_error
    assert not paper.store.list('fills', paper.scope)
    monkeypatch.setattr(paper.store, 'put_many', save)
    quote(paper, '58000')
    assert len(paper.positions) == 1 and not paper.pending_orders
    assert len(paper.store.list('fills', paper.scope)) == 1
    assert paper.store.get('local_order', row['client_id'], scope=paper.scope)['status'] == 'filled'


def test_separate_database_connection_restores_wallet_and_orders(paper):
    submit(paper, size='1', stop_loss='59000')
    submit(paper, size='1', order_type='limit', price='58000')
    store = Store(paper.store.path)
    try:
        restored = PaperBroker(store, None, lambda: paper.specs, lambda: paper.quotes, lambda: None)
        assert restored.state == paper.api.state
        assert restored.snapshot() == paper.api.snapshot()
        assert len(restored.state['pending']) == len(restored.state['algos']) == 1
    finally:
        store.close()


def test_standalone_protection_leverage_and_cancel(paper):
    action(paper, 'account/set-leverage', {'instId': INST, 'mgnMode': 'isolated', 'lever': '5'})
    submit(paper, margin='isolated', size='2')
    assert paper.positions[0]['lever'] == '5'
    request = {'instId': INST, 'tdMode': 'isolated', 'side': 'sell', 'sz': '2',
               'reduceOnly': True, 'ordType': 'oco', 'slTriggerPx': '59000', 'tpTriggerPx': '62000'}
    rows = action(paper, 'trade/order-algo', request)
    action(paper, 'trade/cancel-algos', [{'instId': INST, 'algoId': rows[0]['algoId']}])
    assert not paper.algos
    action(paper, 'trade/order-algo', request)
    paper.change_account('paper')
    assert len(paper.positions) == 1 and len(paper.algos) == 1
    quote(paper, '58000')
    assert not paper.positions and not paper.algos
    assert Decimal(paper.balance['cashBal']) == Decimal('99958.820')


def test_paper_isolation_and_pending_order_survives_reopen(paper):
    row = submit(paper, size='1', order_type='limit', price='59000')
    original = paper.api
    token, _ = paper.trading.prepare(OrderDraft(INST, size='1'), {})
    previous_trading = paper.trading
    paper.change_account('paper')
    assert paper.api is not original and len(paper.pending_orders) == 1
    with pytest.raises(ValueError, match='失效'):
        previous_trading.submit(token, confirmed=True)
    quote(paper, '59000')
    assert paper.store.get('local_order', row['client_id'], scope=paper.scope)['status'] == 'filled'
    assert not paper.store.list('orders', 'demo:unconfigured')
    paper.environment = 'live'
    assert not paper.trading_allowed()
    paper.environment = 'paper'


def test_multiple_protections_cannot_double_close(paper):
    submit(paper, size='1', stop_loss='59000')
    action(paper, 'trade/order-algo', {'instId': INST, 'tdMode': 'cross', 'side': 'sell', 'sz': '1',
           'reduceOnly': True, 'ordType': 'conditional', 'slTriggerPx': '59000'})
    assert len(paper.algos) == 2
    quote(paper, '58000')
    assert not paper.positions and not paper.algos
    assert len(paper.store.list('fills', paper.scope)) == 2


def test_paper_order_events_are_delivered_once(paper):
    paper.save_rule({'name': '模拟成交和撤单', 'instrument': INST, 'mode': 'order',
                     'states': ['filled', 'canceled'], 'enabled': True})
    submit(paper, size='1', stop_loss='59000')
    row = submit(paper, size='1', order_type='limit', price='58000')
    action(paper, 'trade/cancel-order', {'instId': INST, 'ordId': row['order_id']})
    quote(paper, '59000')
    quote(paper, '59000')
    events = [event for _, event in paper.store.list('event', paper.scope)]
    assert len(events) == 3
    assert all(e['source'] == 'paper' and e['name'] == '模拟成交和撤单' for e in events)
    assert sorted(e['order']['state'] for e in events) == ['canceled', 'filled', 'filled']


def test_local_ui_normal_confirmation_flow_and_compact_account_panel(paper, app, monkeypatch):
    from coinpilot_ai.cockpit import ui_trade
    window = Workbench(paper)
    window.show()
    app.processEvents()
    try:
        assert window.title_bar.navigation[1].x() < window.title_bar.view_button.x() < window.title_bar.navigation[2].x()
        window.trade.size.setText('1')
        monkeypatch.setattr(ui_trade, 'confirm', lambda *_: False)
        window.trade.submit()
        assert not paper.positions
        monkeypatch.setattr(ui_trade, 'confirm', lambda *_: True)
        window.trade.submit()
        assert len(paper.positions) == 1 and window.trade.position_table.rowCount() == 1
        dock = window.workspace.docks['info']
        dock.setFloating(True)
        dock.resize(320, 340)
        app.processEvents()
        assert window.trade.tabs.selector.isVisible()
        assert window.trade.tabs.tabBar().isHidden()
        assert window.trade.tabs.selector.count() == 7
        assert window.trade.position_table.horizontalScrollBar().maximum() > 0
        for i in range(7):
            window.trade.tabs.selector.setCurrentIndex(i)
            assert window.trade.tabs.currentIndex() == i
        window.trade.tabs.setCurrentIndex(0)
        window.trade.position_table.selectRow(0)
        window.trade.load_position()
        window.trade.submit()
        assert not paper.positions
    finally:
        window.exiting = True
        window.close()
        window.deleteLater()
        app.processEvents()
