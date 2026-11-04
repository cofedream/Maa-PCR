"""Optional SDK credential reuse, encrypted for the current Windows user."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import tempfile


def _dpapi(data, *, decrypt=False):
    if os.name != 'nt':
        raise OSError('dpapi_unavailable')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    try:
        # UI_FORBIDDEN; omit LOCAL_MACHINE so another Windows user cannot read it.
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            raise OSError('dpapi_failed')
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(buffer, 0, len(data))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(output.data)


class SdkSessionStore:
    def __init__(self, root, server, account_id, username, password):
        identity = f'{server}\0{account_id}'
        self.path = Path(root) / 'data/auth' / (hashlib.sha256(identity.encode()).hexdigest() + '.bin')
        # The credential fingerprint is stored only inside the encrypted payload.
        self.binding = hashlib.sha256(json.dumps(
            [server, account_id, username, password], ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _valid(uid, key):
        return type(uid) is str and 0 < len(uid) <= 512 and type(key) is str and 0 < len(key) <= 4096

    def read(self):
        try:
            if self.path.stat().st_size > 65536:
                return None
            value = json.loads(_dpapi(self.path.read_bytes(), decrypt=True))
            if (type(value) is dict and value.get('version') == 1
                    and value.get('binding') == self.binding
                    and self._valid(value.get('uid'), value.get('access_key'))):
                return value['uid'], value['access_key']
        except (OSError, ValueError, TypeError):
            pass
        return None

    def save(self, uid, key):
        temporary = None
        try:
            if not self._valid(uid, key):
                return False
            encrypted = _dpapi(json.dumps(dict(version=1, binding=self.binding,
                uid=uid, access_key=key), ensure_ascii=False).encode())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            return True
        except (OSError, ValueError, TypeError):
            return False
        finally:
            if temporary is not None:
                try: temporary.unlink(missing_ok=True)
                except OSError: pass

    def clear(self):
        try: self.path.unlink(missing_ok=True)
        except OSError: pass
