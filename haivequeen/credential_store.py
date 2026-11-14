"""User-bound Windows encryption for the Git credential file."""
import base64
import json
import os


def _windows(data, decrypt=False):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; deliberately not machine-wide protection.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError('Cannot unlock credentials for this Windows account' if decrypt else 'Cannot protect credentials for this Windows account')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def encode(credentials):
    raw = json.dumps(credentials).encode('utf-8')
    if os.name == 'nt':
        return json.dumps({'format': 'hq-dpapi-v1', 'data': base64.b64encode(_windows(raw)).decode('ascii')}).encode('utf-8')
    return raw


def decode(contents):
    value = json.loads(contents.decode('utf-8')) if contents else {}
    if value.get('format') == 'hq-dpapi-v1':
        if os.name != 'nt':
            raise OSError('These credentials belong to a Windows account; sign in again on this system')
        return json.loads(_windows(base64.b64decode(value['data'], validate=True), decrypt=True))
    # Existing credentials remain readable; the next save protects the full file.
    return value
