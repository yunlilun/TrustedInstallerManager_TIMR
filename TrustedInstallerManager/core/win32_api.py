import ctypes
import ctypes.wintypes as wintypes
import subprocess
import sys
from PIL import Image
from config import (
    PW_RENDERFULLCONTENT, SRCCOPY, GA_ROOT, GW_HWNDNEXT, DWMWA_CLOAKED, SKIP_WINDOW_CLASSES,
)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
CREATE_NO_WINDOW = 0x08000000
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWCP_DONOTROUND = 1

def is_win11() -> bool:
    try:
        return sys.getwindowsversion().build >= 22000
    except Exception:
        return False

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]

def _query_full_image_name(pid: int, access: int) -> str | None:
    h = ctypes.windll.kernel32.OpenProcess(access, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        ctypes.windll.kernel32.CloseHandle(h)

def _query_via_wmi(pid: int) -> str | None:
    try:
        r = subprocess.run(
            ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ExecutablePath"),
            capture_output=True, text=True, timeout=3, creationflags=CREATE_NO_WINDOW, errors="ignore",
        )
        return (r.stdout or "").strip() or None
    except Exception:
        return None

def get_process_path(pid: int) -> str:
    for access in (PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_QUERY_INFORMATION | PROCESS_VM_READ):
        p = _query_full_image_name(pid, access)
        if p:
            return p
    return _query_via_wmi(pid) or "Unknown Process"

def get_window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value

def get_window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value

def is_window_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    try:
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if hr == 0 and cloaked.value != 0:
            return True
    except Exception:
        pass
    return False

def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    r = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom

def get_root_window(hwnd: int) -> int:
    return ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT) or hwnd

def get_window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value

def is_window(hwnd: int) -> bool:
    return bool(ctypes.windll.user32.IsWindow(hwnd))

def enum_window_at_point(x: int, y: int, self_pid: int) -> int:
    hwnd = ctypes.windll.user32.GetTopWindow(None)
    while hwnd:
        try:
            if (ctypes.windll.user32.IsWindowVisible(hwnd)
                    and not ctypes.windll.user32.IsIconic(hwnd)
                    and not is_window_cloaked(hwnd)
                    and get_window_pid(hwnd) != self_pid):
                rect = get_window_rect(hwnd)
                if rect and rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]:
                    if get_window_class(hwnd) not in SKIP_WINDOW_CLASSES:
                        return hwnd
        except Exception:
            pass
        hwnd = ctypes.windll.user32.GetWindow(hwnd, GW_HWNDNEXT)
    return 0

def hide_window_instantly(hwnd: int):
    # 瞬间隐藏，比 Tkinter 的 withdraw 更快，确保 WindowFromPoint 不会命中自己
    ctypes.windll.user32.ShowWindow(hwnd, 0)

def set_absolute_topmost(hwnd: int):
    try:
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        ctypes.windll.user32.BringWindowToTop(hwnd)
    except Exception:
        pass

def set_window_rounded(hwnd: int, rounded: bool):
    if not is_win11():
        return
    try:
        pref = DWMWCP_ROUND if rounded else DWMWCP_DONOTROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(ctypes.c_int(pref)), ctypes.sizeof(ctypes.c_int)
        )
    except Exception:
        pass

class WindowCapturer:
    def __init__(self) -> None:
        self._last_buf: ctypes.Array | None = None

    def capture(self, hwnd: int) -> Image.Image | None:
        rect = get_window_rect(hwnd)
        if not rect:
            return None
        left, top, right, bottom = rect
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0 or w > 8192 or h > 8192:
            return None
        
        hwnd_dc = ctypes.windll.user32.GetWindowDC(hwnd)
        if not hwnd_dc:
            return None
        mem_dc = ctypes.windll.gdi32.CreateCompatibleDC(hwnd_dc)
        bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
        old = ctypes.windll.gdi32.SelectObject(mem_dc, bmp)
        try:
            ctypes.windll.gdi32.BitBlt(mem_dc, 0, 0, w, h, hwnd_dc, 0, 0, SRCCOPY)
            return self._read_bitmap(mem_dc, bmp, w, h)
        finally:
            if old:
                ctypes.windll.gdi32.SelectObject(mem_dc, old)
            ctypes.windll.gdi32.DeleteObject(bmp)
            ctypes.windll.gdi32.DeleteDC(mem_dc)
            ctypes.windll.user32.ReleaseDC(hwnd, hwnd_dc)

    def _read_bitmap(self, mem_dc: int, bmp: int, w: int, h: int) -> Image.Image:
        bmi = _BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        ctypes.windll.gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        self._last_buf = buf
        return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)

__all__ = [
    "get_process_path", "get_window_class", "get_window_title", "is_window_cloaked",
    "get_window_rect", "get_root_window", "get_window_pid", "is_window",
    "enum_window_at_point", "hide_window_instantly", "set_absolute_topmost", "set_window_rounded", 
    "WindowCapturer", "is_win11",
]