"""Display dates and times using the machine's regional settings."""
import locale

try:
    locale.setlocale(locale.LC_TIME, "")
except locale.Error:
    pass  # Keep the process default if the operating system locale is unavailable.


def date_time(value):
    """Format a local datetime using the system date and time conventions."""
    return value.strftime("%x %X")


def time(value):
    """Format a local datetime using the system time convention."""
    return value.strftime("%X")
