"""Display dates and times using the machine's regional settings."""
import locale
import os

if os.name != "nt":
    try:
        locale.setlocale(locale.LC_TIME, "")
    except locale.Error:
        pass


def _windows_format(value, date):
    """Use Windows user regional overrides without changing the process locale."""
    import ctypes
    from ctypes import wintypes

    class SYSTEMTIME(ctypes.Structure):
        _fields_ = [(name, wintypes.WORD) for name in (
            "year", "month", "day_of_week", "day", "hour", "minute", "second", "milliseconds")]

    value = SYSTEMTIME(value.year, value.month, (value.weekday() + 1) % 7, value.day,
                       value.hour, value.minute, value.second, value.microsecond // 1000)
    function = ctypes.windll.kernel32.GetDateFormatEx if date else ctypes.windll.kernel32.GetTimeFormatEx
    args = (None, 0, ctypes.byref(value), None, None, 0)
    if date:
        args += (None,)
    size = function(*args)
    if not size:
        raise OSError("Windows regional format unavailable")
    buffer = ctypes.create_unicode_buffer(size)
    args = (None, 0, ctypes.byref(value), None, buffer, size)
    if date:
        args += (None,)
    if not function(*args):
        raise OSError("Windows regional format unavailable")
    return buffer.value


def _format(value, date):
    if os.name == "nt":
        try:
            return _windows_format(value, date)
        except (OSError, AttributeError):
            pass
    # nl_langinfo respects the environment without globally changing LC_TIME.
    try:
        pattern = locale.nl_langinfo(locale.D_FMT if date else locale.T_FMT)
    except (AttributeError, ValueError):
        pattern = "%x" if date else "%X"
    return value.strftime(pattern)


def date_time(value):
    """Format a local datetime using the system date and time conventions."""
    return _format(value, True) + " " + _format(value, False)


def time(value):
    """Format a local datetime using the system time convention."""
    return _format(value, False)
