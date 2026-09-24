"""当前用户的 Windows 登录启动项；仅在用户保存设置时修改。"""

from pathlib import Path
import subprocess
import sys

try:
    import winreg
except ImportError:
    winreg = None


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# 保留旧注册表值名，保存新入口时覆盖原启动项，避免双重自启动。
VALUE_NAME = "CryptoWidget"


def startup_command(config_path, cache_dir):
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        arguments = [str(executable)]
    else:
        # 使用当前虚拟环境的无控制台解释器，不依赖 PATH 或登录时的工作目录。
        executable = executable.with_name("pythonw.exe")
        script = Path(__file__).resolve().parent.parent / "coinpilot-ai.py"
        if not executable.is_file() or not script.is_file():
            raise OSError("无法找到 pythonw.exe 或应用入口，请检查 Python 环境和项目目录。")
        arguments = [str(executable), str(script)]
    arguments += ["--config", str(Path(config_path).resolve()),
                  "--cache-dir", str(Path(cache_dir).resolve())]
    command = subprocess.list2cmdline(arguments)
    if len(command) > 260:
        raise OSError("开机启动命令超过 Windows 的 260 字符限制，请将程序或配置移至较短路径。")
    return command


class StartupManager:
    def __init__(self, config_path, cache_dir, registry=None):
        self.config_path = Path(config_path).resolve()
        self.cache_dir = Path(cache_dir).resolve()
        self.registry = winreg if registry is None else registry

    @property
    def supported(self):
        return self.registry is not None

    def read(self):
        """返回原始值和类型，供状态显示与失败回滚使用。"""
        if not self.supported:
            raise OSError("开机自启动仅支持 Windows。")
        api = self.registry
        try:
            with api.OpenKey(api.HKEY_CURRENT_USER, RUN_KEY, 0, api.KEY_READ) as key:
                return api.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise OSError(f"无法读取开机启动项：{exc}") from exc

    def _write(self, entry):
        api = self.registry
        try:
            if entry is None:
                try:
                    with api.OpenKey(api.HKEY_CURRENT_USER, RUN_KEY, 0, api.KEY_SET_VALUE) as key:
                        api.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
            else:
                with api.CreateKeyEx(api.HKEY_CURRENT_USER, RUN_KEY, 0, api.KEY_SET_VALUE) as key:
                    api.SetValueEx(key, VALUE_NAME, 0, entry[1], entry[0])
        except OSError as exc:
            raise OSError(f"无法修改开机启动项：{exc}") from exc

    def save_with(self, enabled, save_config):
        previous = self.read()
        desired = (startup_command(self.config_path, self.cache_dir), self.registry.REG_SZ) if enabled else None
        changed = previous != desired
        if changed:
            self._write(desired)
        try:
            save_config()
        except Exception as exc:
            if changed:
                try:
                    self._write(previous)
                except OSError as rollback_error:
                    raise OSError(f"{exc}；开机启动项回滚失败，请重新检查开机自启动设置。{rollback_error}") from exc
            raise
