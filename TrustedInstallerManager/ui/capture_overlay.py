import os
import time
import tkinter as tk
import ctypes
import ctypes.wintypes as wintypes
from PIL import Image, ImageDraw, ImageTk

from config import (
    MAG_RADIUS, MAG_OFFSET, MAG_CROP_SIZE, CAPTURE_INTERVAL, SKIP_WINDOW_CLASSES,
)
from core.win32_api import (
    WindowCapturer, enum_window_at_point, get_process_path, get_root_window,
    get_window_class, get_window_pid, get_window_rect, get_window_title,
    is_window_cloaked, hide_window_instantly
)

class WindowCaptureOverlay:
    def __init__(self, master, on_target, on_log, on_close=None):
        self._master = master
        self._on_target = on_target
        self._on_log = on_log
        self._on_close = on_close
        self._self_pid = os.getpid()
        self._capturer = WindowCapturer()
        
        self._cache_hwnd = 0
        self._cache_pid = 0
        self._cache_path = ""
        self._cache_img = None
        self._cache_time = 0.0
        self._closed = False
        self._mag_photo = None

        self._build_window()
        self._build_canvas_items()
        self._bind_events()

    def _build_window(self):
        self._top = tk.Toplevel(self._master)
        self._top.overrideredirect(True)
        
        sw = self._top.winfo_screenwidth()
        sh = self._top.winfo_screenheight()
        self._top.geometry(f"{sw}x{sh}+0+0")
        
        # 恢复标准的半透明遮罩，能清晰看到准星和放大镜
        self._top.attributes('-alpha', 0.45)  
        self._top.attributes('-topmost', True)
        self._top.configure(bg="black", cursor="crosshair")
        
        self._top.update_idletasks()
        self._hwnd = int(self._top.wm_frame(), 16)
        
        self._canvas = tk.Canvas(self._top, bg="black", highlightthickness=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        
        # 核心修复：强制获取焦点和捕获鼠标，防止不触发 <Motion> 事件
        self._top.focus_force()
        self._top.grab_set()

    def _build_canvas_items(self):
        c = self._canvas
        mag_r = MAG_RADIUS
        sw = self._top.winfo_screenwidth()
        sh = self._top.winfo_screenheight()
        
        c.create_line(0, sh/2, sw, sh/2, fill="#00FFFF", width=1, dash=(8, 8), tags="cross_h")
        c.create_line(sw/2, 0, sw/2, sh, fill="#00FFFF", width=1, dash=(8, 8), tags="cross_v")
        
        c.create_rectangle(0, 0, 0, 0, outline="#00FF00", width=3, state='hidden', tags="target_rect")
        c.create_rectangle(0, 0, 0, 0, fill="#1A1A1A", outline="#00FF00", width=2, state='hidden', tags="info_bg")
        c.create_text(0, 0, text="", fill="#00FF00", font=("Consolas", 11, "bold"), anchor="w", state='hidden', tags="info_text")
        
        c.create_oval(0, 0, 0, 0, fill="#111111", outline="", tags="mag_bg")
        self._mag_img_id = c.create_image(0, 0, state='hidden', tags="mag_img")
        c.create_line(0, 0, 0, 0, fill="#00FFFF", dash=(4, 4), width=1, tags="mag_h")
        c.create_line(0, 0, 0, 0, fill="#00FFFF", dash=(4, 4), width=1, tags="mag_v")
        c.create_oval(0, 0, 0, 0, outline="#00FFFF", width=2, tags="mag_border")
        c.create_text(0, 0, text="", fill="#00FF00", font=("Consolas", 10, "bold"), anchor="n", tags="mag_text")
        
        mask_big = Image.new('L', (mag_r * 8, mag_r * 8), 0)
        ImageDraw.Draw(mask_big).ellipse((0, 0, mag_r * 8 - 1, mag_r * 8 - 1), fill=255)
        self._mag_mask = mask_big.resize((mag_r * 2, mag_r * 2), Image.Resampling.LANCZOS)

        c.create_text(
            sw / 2, 30,
            text="[ TIMR 窗口捕获模式 ] 移动鼠标选择，单击确认，按 ESC 退出",
            fill="#FF4444", font=("微软雅黑", 14, "bold"), anchor="center"
        )

    def _bind_events(self):
        self._top.bind("<Motion>", self._on_motion)
        self._top.bind("<ButtonPress-1>", self._on_click)
        self._top.bind("<Escape>", lambda e: self.close())

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._top.grab_release()
            self._top.destroy()
        except Exception:
            pass
        if self._on_close:
            try:
                self._on_close()
            except Exception:
                pass

    def _on_motion(self, event):
        try:
            x, y = event.x, event.y
            w = self._top.winfo_width()
            h = self._top.winfo_height()
            c = self._canvas

            c.coords("cross_h", 0, y, w, y)
            c.coords("cross_v", x, 0, x, h)

            raw = enum_window_at_point(x, y, self._self_pid)
            hwnd = get_root_window(raw) if raw else 0
            is_valid = False

            if hwnd:
                rect = get_window_rect(hwnd)
                if rect and rect[2] - rect[0] > 0 and rect[3] - rect[1] > 0:
                    is_valid = True
                    c.itemconfig("target_rect", state='normal')
                    c.coords("target_rect", rect[0], rect[1], rect[2], rect[3])

                    if self._cache_hwnd != hwnd:
                        self._cache_hwnd = hwnd
                        self._cache_pid = get_window_pid(hwnd)
                        self._cache_path = get_process_path(self._cache_pid)
                        self._cache_img = self._capturer.capture(hwnd)
                        self._cache_time = time.time()

                    info_h = 26
                    if rect[1] >= info_h + 5:
                        info_top, info_bottom = rect[1] - info_h - 2, rect[1] - 2
                    else:
                        info_top, info_bottom = rect[1] + 2, rect[1] + info_h + 2
                    
                    c.itemconfig("info_bg", state='normal')
                    c.coords("info_bg", max(rect[0] - 1, 0), info_top, min(rect[2] + 1, w), info_bottom)
                    c.itemconfig("info_text", state='normal', text=f"  {self._cache_path}")
                    c.coords("info_text", rect[0] + 6, (info_top + info_bottom) / 2)

                    now = time.time()
                    if now - self._cache_time > CAPTURE_INTERVAL:
                        self._cache_img = self._capturer.capture(hwnd)
                        self._cache_time = now
            else:
                c.itemconfig("target_rect", state='hidden')
                c.itemconfig("info_bg", state='hidden')
                c.itemconfig("info_text", state='hidden')
                self._cache_hwnd = 0
                self._cache_img = None

            mag_r = MAG_RADIUS
            mag_x, mag_y = x + MAG_OFFSET, y + MAG_OFFSET
            if mag_x + mag_r > w:
                mag_x = x - MAG_OFFSET
            if mag_y + mag_r > h:
                mag_y = y - MAG_OFFSET
            mag_x = max(mag_r + 5, min(mag_x, w - mag_r - 5))
            mag_y = max(mag_r + 5, min(mag_y, h - mag_r - 5))

            c.coords("mag_bg", mag_x - mag_r, mag_y - mag_r, mag_x + mag_r, mag_y + mag_r)
            c.coords("mag_border", mag_x - mag_r, mag_y - mag_r, mag_x + mag_r, mag_y + mag_r)
            c.coords("mag_h", mag_x - mag_r, mag_y, mag_x + mag_r, mag_y)
            c.coords("mag_v", mag_x, mag_y - mag_r, mag_x, mag_y + mag_r)
            c.coords("mag_text", mag_x, mag_y + mag_r + 6)
            c.itemconfig("mag_text", text=f"{x} x {y}")

            if is_valid and self._cache_img and self._cache_hwnd == hwnd:
                rect = get_window_rect(self._cache_hwnd)
                if rect:
                    mx = x - rect[0]
                    my = y - rect[1]
                    half = MAG_CROP_SIZE // 2
                    src_w, src_h = self._cache_img.size
                    box = (mx - half, my - half, mx + half, my + half)
                    sx1, sy1 = max(0, box[0]), max(0, box[1])
                    sx2, sy2 = min(src_w, box[2]), min(src_h, box[3])
                    
                    crop_canvas = Image.new('RGB', (MAG_CROP_SIZE, MAG_CROP_SIZE), (0, 0, 0))
                    if sx2 > sx1 and sy2 > sy1:
                        crop_canvas.paste(
                            self._cache_img.crop((sx1, sy1, sx2, sy2)),
                            (sx1 - box[0], sy1 - box[1]),
                        )
                    crop_canvas = crop_canvas.resize((mag_r * 2, mag_r * 2), Image.Resampling.NEAREST)
                    crop_canvas.putalpha(self._mag_mask)
                    
                    self._mag_photo = ImageTk.PhotoImage(crop_canvas)
                    c.itemconfig("mag_img", state='normal', image=self._mag_photo)
                    c.coords("mag_img", mag_x, mag_y)
                else:
                    c.itemconfig("mag_img", state='hidden')
            else:
                c.itemconfig("mag_img", state='hidden')

        except Exception as e:
            pass

    def _on_click(self, event):
        x, y = event.x_root, event.y_root
        
        try:
            self._top.grab_release()
        except Exception:
            pass
            
        hide_window_instantly(self._hwnd)
        self._top.update_idletasks()
        time.sleep(0.05)

        pt = wintypes.POINT(x, y)
        hwnd = ctypes.windll.user32.WindowFromPoint(pt)
        
        if not hwnd:
            hwnd = enum_window_at_point(x, y, self._self_pid)

        self.close()

        if not hwnd:
            self._on_log("未捕获到有效窗口（鼠标位于桌面或任务栏）。")
            return
            
        hwnd = get_root_window(hwnd)
        if is_window_cloaked(hwnd):
            self._on_log("窗口处于后台，已忽略。")
            return
        cls = get_window_class(hwnd)
        if cls in SKIP_WINDOW_CLASSES:
            self._on_log(f"系统外壳类名 '{cls}'，已忽略。")
            return
        pid = get_window_pid(hwnd)
        if pid == self._self_pid:
            self._on_log("捕获到程序自身，已忽略。")
            return
            
        path = get_process_path(pid)
        if path == "Unknown Process":
            self._on_log(f"无法获取进程信息 (PID: {pid})。")
            return
            
        title = get_window_title(hwnd)
        self._on_target(hwnd, pid, path, cls, title)

__all__ = ["WindowCaptureOverlay"]