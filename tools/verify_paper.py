"""使用临时虚拟账户检查本地模拟下单和窄账户面板，不访问网络。"""
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QTest
from coinpilot_ai.app import create_application
from coinpilot_ai.config import DEFAULT_CONFIG
from coinpilot_ai.cockpit.service import CockpitService
from coinpilot_ai.cockpit.store import Store
from coinpilot_ai.cockpit.workbench import Workbench


class NoVault:
    def read(self, _key):
        raise AssertionError('不应读取凭据')


def main():
    app = create_application([])
    for name in ('msyh.ttc', 'msyhbd.ttc'):
        QFontDatabase.addApplicationFont(str(Path(os.environ['SystemRoot'])/'Fonts'/name))
    output = ROOT/'artifacts'/'paper-trading'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        store = Store(folder/'paper.db')
        inst = 'BTC-USDT-SWAP'
        store.put('settings', 'workbench', {'environment': 'paper', 'watchlist': [inst], 'scenes': {}})
        store.close()
        service = CockpitService(dict(DEFAULT_CONFIG, proxy_enabled=False), folder/'paper.db', folder/'icons', vault=NoVault(), autostart=False)
        service.refresh_market = lambda: None
        service.fetch_candles = lambda *_: None
        def network_forbidden(*_a, **_k):
            raise AssertionError('离线验证不得联网')
        service.transport.request = network_forbidden
        service.specs[inst] = {'instId': inst, 'state': 'live', 'ctType': 'linear', 'settleCcy': 'USDT',
                               'ctVal': '0.01', 'ctValCcy': 'BTC', 'lotSz': '0.01', 'minSz': '0.01', 'tickSz': '0.1'}
        now = time.time()
        service.candles[(inst, '15m')] = [[str(int((now-(180-i)*900)*1000)), str(p), str(p+100), str(p-100), str(p+30), '90', '0', '0', '1']
                                         for i in range(180) for p in [58000+i*10+math.sin(i/7)*200]]
        service.market.exchange_times['BTCUSDT'] = now
        service.candle_times[(inst, '15m')] = now
        service.chart_feed.stream_state = '实时（本地模拟示例）'
        service._price('BTCUSDT', '60000', '', 'okx')
        window = Workbench(service)
        try:
            window.resize(1440, 900)
            window.show()
            app.processEvents()
            with patch('coinpilot_ai.cockpit.ui_trade.confirm', return_value=True):
                window.trade.size.setText('2')
                window.trade.sl.setText('59000')
                window.trade.tp.setText('62000')
                window.trade.submit()
            assert len(service.positions) == 1 and len(service.algos) == 1
            service.market.exchange_times['BTCUSDT'] = time.time()
            service._price('BTCUSDT', '60100', '', 'okx')
            QTest.qWait(80)
            app.processEvents()
            window.grab().save(str(output/'workspace.png'))
            dock = window.workspace.docks['info']
            dock.setFloating(True)
            dock.resize(360, 320)
            app.processEvents()
            assert window.trade.tabs.selector.isVisible()
            dock.grab().save(str(output/'account-narrow.png'))
            window.trade.tabs.selector.setCurrentIndex(2)
            app.processEvents()
            dock.grab().save(str(output/'protection-narrow.png'))
            window.open_settings('OKX 账户')
            app.processEvents()
            window.grab().save(str(output/'settings.png'))
            assert not window.settings_page.api_key.isEnabled()
            print('PASS: offline confirmed paper order, protection, narrow selector and settings')
        finally:
            window.exiting = True
            window.close()
            service.close()
            window.deleteLater()
            service.deleteLater()
            app.processEvents()


if __name__ == '__main__':
    main()
