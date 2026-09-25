"""只读检查工作台使用的 OKX 公开合约、K 线、报价接口；不读取任何密钥。"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PyQt6.QtCore import QCoreApplication, QTimer
from coinpilot_ai.core.config import DEFAULT_CONFIG
from coinpilot_ai.integrations.okx import OkxClient
from coinpilot_ai.integrations.transport import JsonTransport
from coinpilot_ai.market.candles import CandleStream


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct", action="store_true")
    args = parser.parse_args()
    app = QCoreApplication([])
    transport = JsonTransport()
    transport.set_proxy(dict(DEFAULT_CONFIG, proxy_enabled=not args.direct))
    api = OkxClient(transport)
    outstanding, failures = {"instruments", "candles", "history", "ticker", "candle_ws"}, []
    def done(name, rows, error):
        if error:
            failures.append(name)
            print(name + ": " + str(error))
        else:
            try:
                if name == "instruments":
                    assert any(r["instId"] == "BTC-USDT-SWAP" and r["ctType"] == "linear" for r in rows)
                elif name in ("candles", "history"):
                    assert len(rows) >= 21 and all(len(r) >= 9 for r in rows)
                elif name == "candle_ws":
                    assert rows and len(rows[0]) == 9
                else:
                    assert rows[0]["instId"] == "BTC-USDT-SWAP" and int(rows[0]["ts"]) > 0
                print(name + ": OK")
            except (AssertionError, ValueError, KeyError, IndexError):
                failures.append(name)
                print(name + ": 返回数据不满足工作台要求")
        outstanding.discard(name)
        if not outstanding:
            app.quit()
    def stream_rows(pair, rows):
        if "candle_ws" in outstanding:
            done("candle_ws", rows, None)
    stream = CandleStream(transport.manager.proxy(), stream_rows, lambda _: None)
    stream.select(("BTC-USDT-SWAP", "15m"))
    for name, path, params in (("instruments", "/api/v5/public/instruments", {"instType": "SWAP"}),
                               ("candles", "/api/v5/market/candles", {"instId": "BTC-USDT-SWAP", "bar": "15m", "limit": "30"}),
                               ("history", "/api/v5/market/history-candles", {"instId": "BTC-USDT-SWAP", "bar": "15m", "limit": "300"}),
                               ("ticker", "/api/v5/market/ticker", {"instId": "BTC-USDT-SWAP"})):
        api.get(path, lambda rows, error, key=name: done(key, rows, error), params)
    QTimer.singleShot(20000, app.quit)
    app.exec()
    stream.close()
    transport.close()
    return 1 if outstanding or failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
