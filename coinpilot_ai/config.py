"""配置兼容、校验与原子保存；导入模块不会改动用户文件。"""

import json
import ipaddress
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from datetime import datetime
from uuid import uuid4

# 沿用改名前的数据路径，升级后继续读取原设置及同路径派生的数据库。
SETTINGS_FILE = Path.home() / "crypto_widget_settings.json"
ICON_CACHE_DIR = Path.home() / "crypto_widget_icons"
DEFAULT_CONFIG = {
    "symbol1": "BTCUSDT", "symbol2": "ETHUSDT", "symbol3": "SOLUSDT",
    "decimals1": 2, "decimals2": 2, "decimals3": 2,
    "text_size": 24, "bg_opacity": 0.7, "update_interval": 10,
    "cycle_interval": 3, "cycle_enabled": True, "mini_mode": True, "pos_x": 200, "pos_y": 200,
    "hide_hotkey": "Alt+Z",
    "price_source": "auto",
    "ui_theme": "okx_dark",
    "auto_check_updates": True,
    "proxy_enabled": True, "proxy_type": "socks5", "proxy_host": "127.0.0.1", "proxy_port": 7897,
}
PROXY_LABELS = {"socks5": "SOCKS5", "http": "HTTP"}
SOURCE_LABELS = {"auto": "自动（三源）", "binance": "币安（Binance）", "okx": "欧易（OKX）", "bybit": "Bybit"}
LIMITS = {
    "text_size": (8, 64), "bg_opacity": (0, 1), "update_interval": (5, 300),
    "cycle_interval": (3, 60), "pos_x": (-100000, 100000), "pos_y": (-100000, 100000),
    **{f"decimals{i}": (0, 8) for i in range(1, 4)},
}


def normalize_proxy_host(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("请输入代理主机地址，例如 127.0.0.1；不要包含协议或端口。")
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        hostname = value.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("代理主机地址无效。") from None
    if len(hostname) > 253 or not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
        for part in hostname.rstrip(".").split(".")
    ):
        raise ValueError("代理主机地址无效，请仅填写 IP 地址或主机名。")
    return hostname


def normalize_symbol(value):
    if not isinstance(value, str):
        raise ValueError("请输入交易对代码，例如 BTCUSDT。")
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,30}", value):
        raise ValueError("交易对需为 2～30 位英文字母或数字，例如 BTCUSDT。")
    return value


def normalize_hotkey(value):
    if not isinstance(value, str):
        raise ValueError("请输入快捷键，例如 Alt+Z。")
    parts = [part.strip().upper() for part in value.split("+")]
    modifiers, key = parts[:-1], parts[-1]
    if (not modifiers or len(modifiers) != len(set(modifiers))
            or any(part not in ("CTRL", "ALT", "SHIFT", "WIN") for part in modifiers)
            or not any(part in ("CTRL", "ALT", "WIN") for part in modifiers)
            or not re.fullmatch(r"[A-Z0-9]|F(?:[1-9]|1[0-9]|2[0-4])", key)):
        raise ValueError("使用 Ctrl、Alt 或 Win 搭配字母、数字或 F1～F24，例如 Alt+Z。")
    return "+".join([part.title() for part in ("CTRL", "ALT", "SHIFT", "WIN") if part in modifiers] + [key])


def validate_config(raw):
    """恢复合法配置；无效类型回退默认值，合法数值限制在支持范围内。"""
    result = DEFAULT_CONFIG.copy()
    corrected = []
    for key, default in DEFAULT_CONFIG.items():
        if key not in raw:
            continue
        value = raw[key]
        try:
            if key.startswith("symbol"):
                value = normalize_symbol(value)
            elif key == "hide_hotkey":
                value = normalize_hotkey(value)
            elif key == "proxy_host":
                value = normalize_proxy_host(value)
            elif key == "proxy_type":
                if not isinstance(value, str) or value not in PROXY_LABELS:
                    raise ValueError()
            elif key == "proxy_port":
                if type(value) is not int or not 1 <= value <= 65535:
                    raise ValueError()
            elif key == "price_source":
                if not isinstance(value, str) or value not in SOURCE_LABELS:
                    raise ValueError()
            elif key == "ui_theme":
                from .theme import get_theme
                if not isinstance(value, str) or get_theme(value).id != value:
                    raise ValueError()
            elif key in ("cycle_enabled", "mini_mode", "proxy_enabled", "auto_check_updates"):
                if type(value) is not bool:
                    raise ValueError()
            else:
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError()
                if key != "bg_opacity" and int(value) != value:
                    raise ValueError()
                low, high = LIMITS[key]
                clamped = min(high, max(low, value))
                if clamped != value:
                    corrected.append(key)
                value = float(clamped) if key == "bg_opacity" else int(clamped)
            result[key] = value
        except (ValueError, TypeError, OverflowError):
            result[key] = default
            corrected.append(key)
    return result, corrected


class SettingsStore:
    def __init__(self, path=SETTINGS_FILE):
        self.path = Path(path)
        self.warning = ""

    def load(self):
        self.warning = ""
        if not self.path.exists():
            return DEFAULT_CONFIG.copy()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("配置内容必须是对象")
        except (ValueError, UnicodeError):
            backup = self.path.with_name(
                f"{self.path.name}.broken-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}.bak"
            )
            try:
                shutil.copy2(self.path, backup)
                self.warning = f"配置文件损坏，已使用默认设置。原文件已备份至：\n{backup}"
            except OSError as exc:
                self.warning = f"配置文件损坏，已使用默认设置。备份失败：{exc}"
            return DEFAULT_CONFIG.copy()
        except OSError as exc:
            self.warning = f"无法读取配置，已使用默认设置：{exc}"
            return DEFAULT_CONFIG.copy()
        result, corrected = validate_config(raw)
        if corrected:
            self.warning = "部分配置值无效或超出支持范围，已自动修正。请在设置中检查并保存。"
        return result

    def save(self, config):
        validated, corrected = validate_config(config)
        if corrected:
            raise ValueError("配置值无效，请检查后重试。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(validated, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
