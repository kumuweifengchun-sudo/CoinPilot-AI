"""只检查真实 WebSocket 推送，不使用 HTTP 兜底或用户配置。"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QCoreApplication, QTimer
from PyQt6.QtNetwork import QNetworkProxy

from coinpilot_ai.market.providers import SOURCE_ORDER
from coinpilot_ai.market.streaming import PriceStream


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("all", "auto", *SOURCE_ORDER), default="all")
    parser.add_argument("--direct", action="store_true", help="不使用默认 SOCKS5 127.0.0.1:7897 代理")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    app = QCoreApplication([])
    proxy = (QNetworkProxy(QNetworkProxy.ProxyType.NoProxy) if args.direct else
             QNetworkProxy(QNetworkProxy.ProxyType.Socks5Proxy, "127.0.0.1", 7897))
    modes = SOURCE_ORDER if args.source == "all" else (args.source,)
    results, streams = {}, []

    def received(mode, symbol, value, source):
        results[f"{mode}:{symbol}"] = {"price": str(value), "source": source}
        if len(results) == len(streams):
            app.quit()

    for mode in modes:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            stream = PriceStream(symbol, mode, proxy)
            stream.price_ready.connect(lambda symbol, value, source, mode=mode: received(mode, symbol, value, source))
            streams.append(stream)
    for stream in streams:
        stream.start()
    QTimer.singleShot(max(1, args.timeout) * 1000, app.quit)
    app.exec()
    for mode in modes:
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            results.setdefault(f"{mode}:{symbol}", {"error": "规定时间内未收到 WebSocket 报价"})
    for stream in streams:
        stream.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return int(any("error" in result for result in results.values()))


if __name__ == "__main__":
    sys.exit(main())
