"""对真实行情和图标服务进行有时限的连通性验证，不使用用户缓存。"""

import json
import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QCoreApplication, QTimer
from coinpilot_ai.market.client import MarketClient
from coinpilot_ai.market.providers import SOURCE_ORDER


def main():
    parser = argparse.ArgumentParser(description="公开永续合约行情数据源连通性检查")
    parser.add_argument("--source", choices=("all", "auto", *SOURCE_ORDER), default="all")
    args = parser.parse_args()
    app = QCoreApplication([])
    modes = SOURCE_ORDER if args.source == "all" else (args.source,)
    results = {"prices": {mode: {} for mode in modes}, "icons": None}
    with tempfile.TemporaryDirectory(prefix="coinpilot-ai-network-") as cache:
        clients = {mode: MarketClient(cache, source=mode, streaming=False) for mode in modes}
        def finished():
            if all(len(prices) == 3 for prices in results["prices"].values()) and results["icons"] is not None:
                app.quit()
        def price(mode, symbol, value, error, source):
            results["prices"][mode][symbol] = {"price": str(value) if value is not None else None,
                                              "error": error, "source": source}
            finished()
        def icons(success, failure):
            results["icons"] = {"success": success, "failure": failure}
            finished()
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        for mode, client in clients.items():
            client.price_ready.connect(lambda symbol, value, error, source, mode=mode: price(mode, symbol, value, error, source))
            client.refresh_prices(symbols)
        first_client = next(iter(clients.values()))
        first_client.icons_finished.connect(icons)
        first_client.reload_icons(symbols)
        QTimer.singleShot(12000, app.quit)
        app.exec()
        results["unfinished_requests"] = sum(len(client.pending) for client in clients.values())
        for client in clients.values():
            client.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
