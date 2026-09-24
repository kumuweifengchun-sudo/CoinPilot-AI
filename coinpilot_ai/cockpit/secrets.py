"""仅使用 Windows Credential Manager；没有明文降级路径。"""
import ctypes
import json
import sys
from ctypes import wintypes


class Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


class CredentialVault:
    # 旧命名空间属于持久化协议，不能随应用展示名称改变。
    def __init__(self, namespace="CryptoWidget"):
        self.namespace = namespace

    def _api(self):
        if sys.platform != "win32":
            raise OSError("密钥存储需要 Windows 凭据管理器")
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(Credential))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        return api

    def write(self, key, value):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(raw) > 2560:
            raise ValueError("凭据内容过长")
        buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        cred = Credential(Type=1, TargetName=f"{self.namespace}/{key}",
                          CredentialBlobSize=len(raw), CredentialBlob=buffer,
                          Persist=2, UserName="CoinPilotAI")
        if not self._api().CredWriteW(ctypes.byref(cred), 0):
            raise OSError("无法保存 Windows 凭据，错误码 " + str(ctypes.get_last_error()))

    def read(self, key):
        api = self._api()
        ptr = ctypes.POINTER(Credential)()
        if not api.CredReadW(f"{self.namespace}/{key}", 1, 0, ctypes.byref(ptr)):
            error = ctypes.get_last_error()
            if error == 1168:
                return None
            raise OSError("无法读取 Windows 凭据，错误码 " + str(error))
        try:
            return json.loads(ctypes.string_at(ptr.contents.CredentialBlob,
                                              ptr.contents.CredentialBlobSize).decode("utf-8"))
        finally:
            api.CredFree(ptr)

    def delete(self, key):
        if not self._api().CredDeleteW(f"{self.namespace}/{key}", 1, 0):
            if ctypes.get_last_error() != 1168:
                raise OSError("无法删除 Windows 凭据")
