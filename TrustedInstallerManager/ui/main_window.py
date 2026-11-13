import os
import string
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk, colorchooser

from tkinterdnd2 import DND_FILES

from config import (
    APP_TITLE, APP_VERSION, get_log_dir, save_config, DEFAULT_THEMES, get_config_path, get_base_path
)
from core.executor import (
    PowerShellExecutor, SecurityChecker, force_move, grant_and_open, grant_and_open_many, validate_move,
)
from core.win32_api import is_window, set_absolute_topmost, set_window_rounded, is_win11
from ui.capture_overlay import WindowCaptureOverlay

CREATE_NO_WINDOW = 0x08000000

class RoundedButton(tk.Canvas):
    def __init__(self, master, text="", command=None, bg="#1E1E1E", fg="#FFFFFF", 
                 hover_bg="#3C3C3C", press_bg="#505050", radius=8, font=("微软雅黑", 9), **kwargs):
        super().__init__(master, bg=master["bg"], highlightthickness=0, **kwargs)
        self.text = text
        self.command = command
        self.bg_color = bg
        self.fg_color = fg
        self.hover_color = hover_bg
        self.press_color = press_bg
        self.radius = radius
        self.font = font
        self.current_bg = bg
        self._state = tk.NORMAL
        
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _draw(self, event=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1: return
        r = min(self.radius, w//2, h//2)
        points = [r, 0, w-r, 0, w, 0, w, r, w, h-r, w, h, w-r, h, r, h, 0, h, 0, h-r, 0, r, 0, 0]
        self.create_polygon(points, smooth=True, fill=self.current_bg, outline="")
        self.create_text(w//2, h//2, text=self.text, fill=self.fg_color, font=self.font)

    def _on_enter(self, event):
        if self._state == tk.NORMAL:
            self.current_bg = self.hover_color
            self._draw()

    def _on_leave(self, event):
        if self._state == tk.NORMAL:
            self.current_bg = self.bg_color
            self._draw()

    def _on_press(self, event):
        if self._state == tk.NORMAL:
            self.current_bg = self.press_color
            self._draw()

    def _on_release(self, event):
        if self._state == tk.NORMAL:
            self.current_bg = self.hover_color
            self._draw()
            if self.command: self.command()

    def set_colors(self, bg, hover_bg, press_bg):
        self.bg_color = bg
        self.hover_color = hover_bg
        self.press_color = press_bg
        self.current_bg = bg
        self._draw()

    def config_state(self, state):
        self._state = state
        self.configure(cursor="hand2" if state == tk.NORMAL else "arrow")
        if state == tk.DISABLED:
            self.current_bg = "#2A2A2A"
            self.fg_color = "#666666"
        else:
            self.current_bg = self.bg_color
            self.fg_color = "#FFFFFF"
        self._draw()


class CanvasProgressBar(tk.Canvas):
    def __init__(self, master, bg="#1E1E1E", fg="#00FF00", **kwargs):
        super().__init__(master, bg=bg, highlightthickness=0, height=24, **kwargs)
        self.fg = fg
        self.progress = 0
        self.text = "就绪"
        self.bind("<Configure>", self._draw)
        
    def set(self, progress, text=""):
        self.progress = max(0, min(100, progress))
        if text: self.text = text
        self._draw()
        
    def _draw(self, event=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1: return
        self.create_rectangle(0, 0, w, h, fill="#2D2D2D", outline="#444444", width=1)
        fill_w = (w - 2) * (self.progress / 100.0)
        if fill_w > 0:
            self.create_rectangle(1, 1, fill_w + 1, h - 1, fill=self.fg, outline="")
        text_color = "#FFFFFF" if self.progress > 40 else "#AAAAAA"
        self.create_text(w/2, h/2, text=f"{self.text} ({int(self.progress)}%)", fill=text_color, font=("微软雅黑", 9, "bold"))


class ThemeManager:
    def __init__(self, root, target_color):
        self.root = root
        self.current_color = target_color
        self.widgets_to_color = []
        self.bg_widgets = []
        self.progress_widgets = []

    def register(self, widget, is_primary=False):
        self.widgets_to_color.append((widget, is_primary))

    def register_bg(self, widget):
        self.bg_widgets.append(widget)
        
    def register_progress(self, widget):
        self.progress_widgets.append(widget)

    def _hex_to_rgb(self, h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def _rgb_to_hex(self, rgb):
        return '#{:02x}{:02x}{:02x}'.format(*rgb)

    def _interpolate(self, c1, c2, t):
        r1, g1, b1 = self._hex_to_rgb(c1)
        r2, g2, b2 = self._hex_to_rgb(c2)
        return self._rgb_to_hex((int(r1 + (r2 - r1) * t), int(g1 + (g2 - g1) * t), int(b1 + (b2 - b1) * t)))

    def transition_to(self, new_color, animate=True, callback=None):
        if not animate or new_color == self.current_color:
            self.current_color = new_color
            self._apply_color(self.current_color)
            if callback: callback()
            return
        start_color = self.current_color
        steps = 10
        delay = 20
        def _step(step):
            t = step / steps
            interp = self._interpolate(start_color, new_color, t)
            self._apply_color(interp)
            if step < steps:
                self.root.after(delay, lambda: _step(step + 1))
            else:
                self.current_color = new_color
                if callback: callback()
        _step(0)

    def _apply_color(self, color):
        for w in self.progress_widgets:
            try:
                if w.winfo_exists():
                    w.fg = color
                    w._draw()
            except tk.TclError: pass
        for w, is_primary in self.widgets_to_color:
            try:
                if w.winfo_exists():
                    if isinstance(w, RoundedButton):
                        if is_primary:
                            hover = self._interpolate(color, "#FFFFFF", 0.2)
                            press = self._interpolate(color, "#000000", 0.2)
                            w.set_colors(color, hover, press)
                        else:
                            w.set_colors("#3C3C3C", "#505050", "#2A2A2A")
                    else:
                        if is_primary: w.configure(selectcolor=color)
            except tk.TclError: pass


class SettingsWindow:
    def __init__(self, master, cfg, theme_manager, on_save):
        self.top = tk.Toplevel(master)
        self.top.title("系统设置")
        self.top.geometry("380x620")
        self.top.configure(bg="#1E1E1E")
        self.top.transient(master)
        self.top.grab_set()

        self.cfg = cfg
        self.theme_mgr = theme_manager
        self.on_save = on_save

        tk.Label(self.top, text="主题颜色", bg="#1E1E1E", fg="#FFFFFF", font=("微软雅黑", 11, "bold")).pack(pady=(15, 5))
        color_frame = tk.Frame(self.top, bg="#1E1E1E")
        color_frame.pack(fill=tk.X, padx=20)
        for i, (name, color) in enumerate(DEFAULT_THEMES.items()):
            btn = tk.Button(color_frame, bg=color, width=4, relief="flat", command=lambda c=color: self._pick_theme(c))
            btn.grid(row=i//4, column=i%4, padx=5, pady=5)

        self.color_preview = tk.Label(self.top, text="当前主题色", bg=cfg.theme_color, fg="#FFFFFF", pady=5)
        self.color_preview.pack(fill=tk.X, padx=20, pady=10)
        tk.Button(self.top, text="导入自定义颜色", bg="#3C3C3C", fg="#FFFFFF", relief="flat", command=self._custom_color).pack(pady=5)

        tk.Label(self.top, text="强杀引擎选择", bg="#1E1E1E", fg="#FFFFFF", font=("微软雅黑", 10)).pack(pady=(15, 5))
        self.var_engine = tk.StringVar(value=cfg.kill_engine)
        engine_frame = tk.Frame(self.top, bg="#1E1E1E")
        engine_frame.pack()
        tk.Radiobutton(engine_frame, text="自动 (优先第三方)", variable=self.var_engine, value="auto", bg="#1E1E1E", fg="#FFFFFF", selectcolor="#094771").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(engine_frame, text="纯自身", variable=self.var_engine, value="native", bg="#1E1E1E", fg="#FFFFFF", selectcolor="#094771").pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(engine_frame, text="仅第三方", variable=self.var_engine, value="psexec", bg="#1E1E1E", fg="#FFFFFF", selectcolor="#094771").pack(side=tk.LEFT, padx=5)

        tk.Label(self.top, text="UI 风格", bg="#1E1E1E", fg="#FFFFFF", font=("微软雅黑", 10)).pack(pady=(15, 5))
        self.var_ui = tk.StringVar(value=cfg.ui_style)
        style_frame = tk.Frame(self.top, bg="#1E1E1E")
        style_frame.pack()
        tk.Radiobutton(style_frame, text="Windows 11", variable=self.var_ui, value="win11", bg="#1E1E1E", fg="#FFFFFF", selectcolor="#094771").pack(side=tk.LEFT, padx=10)
        tk.Radiobutton(style_frame, text="Windows 10", variable=self.var_ui, value="win10", bg="#1E1E1E", fg="#FFFFFF", selectcolor="#094771").pack(side=tk.LEFT, padx=10)

        self.var_topmost = tk.BooleanVar(value=cfg.force_topmost)
        self.var_anim = tk.BooleanVar(value=cfg.enable_animation)
        self.var_round = tk.BooleanVar(value=cfg.window_rounded)

        tk.Checkbutton(self.top, text="开启绝对置顶 (压制安全软件)", variable=self.var_topmost, bg="#1E1E1E", fg="#FFFFFF", selectcolor="#3C3C3C", activebackground="#1E1E1E", activeforeground="#FFFFFF").pack(pady=(15, 5))
        tk.Checkbutton(self.top, text="开启主题过渡动画", variable=self.var_anim, bg="#1E1E1E", fg="#FFFFFF", selectcolor="#3C3C3C", activebackground="#1E1E1E", activeforeground="#FFFFFF").pack(pady=5)
        
        round_cb = tk.Checkbutton(self.top, text="启用四周圆角窗口", variable=self.var_round, bg="#1E1E1E", fg="#FFFFFF", selectcolor="#3C3C3C", activebackground="#1E1E1E", activeforeground="#FFFFFF")
        round_cb.pack(pady=5)
        if not is_win11():
            round_cb.config(state=tk.DISABLED, text="启用四周圆角 (Win10 不支持)", fg="#666666")

        btn_frame = tk.Frame(self.top, bg="#1E1E1E")
        btn_frame.pack(pady=20)
        tk.Button(btn_frame, text="保存并应用", bg="#005A9E", fg="#FFFFFF", relief="flat", command=self._save).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="恢复默认设置", bg="#8B0000", fg="#FFFFFF", relief="flat", command=self._reset_settings).pack(side=tk.LEFT, padx=5)

    def _pick_theme(self, color):
        self.cfg.theme_color = color
        self.color_preview.configure(bg=color)
        self.theme_mgr.transition_to(color, self.cfg.enable_animation)

    def _custom_color(self):
        color = colorchooser.askcolor(title="选择自定义颜色", color=self.cfg.theme_color)[1]
        if color: self._pick_theme(color)

    def _save(self):
        self.cfg.force_topmost = self.var_topmost.get()
        self.cfg.enable_animation = self.var_anim.get()
        self.cfg.ui_style = self.var_ui.get()
        self.cfg.window_rounded = self.var_round.get()
        self.cfg.kill_engine = self.var_engine.get()
        self.top.destroy() 
        self.on_save()

    def _reset_settings(self):
        if messagebox.askyesno("确认", "将删除所有自定义配置并恢复默认，程序将退出。是否继续？", parent=self.top):
            try:
                path = get_config_path()
                if os.path.exists(path): os.remove(path)
            except Exception as e:
                messagebox.showerror("错误", f"删除失败: {e}", parent=self.top)
            self.top.destroy()
            self.top.master.destroy()


class TaskManagerWindow:
    def __init__(self, master, cfg, log_cb):
        self.top = tk.Toplevel(master)
        self.top.title("任务管理器 - 进程列表")
        self.top.geometry("700x500")
        self.top.configure(bg="#1E1E1E")
        self.top.transient(master)
        self.cfg = cfg
        self.log = log_cb

        toolbar = tk.Frame(self.top, bg="#2D2D2D", pady=5, padx=5)
        toolbar.pack(fill=tk.X)
        
        tk.Label(toolbar, text="搜索进程: ", bg="#2D2D2D", fg="#FFFFFF").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(toolbar, textvariable=self.search_var, bg="#1E1E1E", fg="#00FF00", insertbackground="#FFFFFF", relief="flat")
        self.search_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.search_var.trace_add("write", lambda *args: self._refresh_list())

        btn_refresh = tk.Button(toolbar, text="刷新列表", bg="#3C3C3C", fg="#FFFFFF", relief="flat", command=self._refresh_list)
        btn_refresh.pack(side=tk.RIGHT, padx=5)
        
        btn_kill = tk.Button(toolbar, text="结束选中进程", bg="#8B0000", fg="#FFFFFF", relief="flat", command=self._kill_selected)
        btn_kill.pack(side=tk.RIGHT, padx=5)

        style = ttk.Style()
        style.configure("TM.Treeview", background="#252526", foreground="#CCCCCC", fieldbackground="#252526", rowheight=24, font=("Consolas", 10))
        style.map("TM.Treeview", background=[('selected', '#094771')], foreground=[('selected', '#FFFFFF')])

        self.tree = ttk.Treeview(self.top, columns=("name", "pid", "mem"), show="headings", style="TM.Treeview")
        self.tree.heading("name", text="进程名称")
        self.tree.heading("pid", text="PID")
        self.tree.heading("mem", text="内存使用")
        self.tree.column("name", width=300)
        self.tree.column("pid", width=100)
        self.tree.column("mem", width=150, anchor="center")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._refresh_list()

    def _refresh_list(self):
        for item in self.tree.get_children(): self.tree.delete(item)
        keyword = self.search_var.get().lower()
        
        # 使用 tasklist 获取进程列表，无黑框
        try:
            r = subprocess.run(
                ("tasklist", "/FO", "CSV", "/NH"),
                capture_output=True, text=True, encoding="gbk", errors="ignore",
                creationflags=CREATE_NO_WINDOW
            )
            for line in r.stdout.splitlines():
                parts = line.strip('"').split('","')
                if len(parts) >= 5:
                    name, pid, _, _, mem = parts[:5]
                    if keyword in name.lower() or keyword in pid:
                        self.tree.insert("", "end", values=(name, pid, mem))
        except Exception:
            pass

    def _kill_selected(self):
        selected = self.tree.selection()
        if not selected: return
        item = self.tree.item(selected[0], "values")
        name, pid = item[0], item[1]
        
        if not messagebox.askyesno("确认", f"确定要强制结束 {name} (PID: {pid}) 吗？", parent=self.top):
            return
            
        self.log(f"尝试从任务管理器结束进程: {name} (PID: {pid})")
        
        # 直接复用主窗口的 kill 逻辑
        if self._execute_kill(pid, name):
            messagebox.showinfo("成功", f"进程 {name} 已结束。", parent=self.top)
            self._refresh_list()
        else:
            messagebox.showerror("失败", f"无法结束 {name}，可能受系统保护。", parent=self.top)

    def _execute_kill(self, pid, name):
        # 这是一个简化的强杀逻辑，供弹窗直接使用
        engine = self.cfg.kill_engine
        psexec_path = os.path.join(get_base_path(), "Coreengine", "PSTools", "PsExec64.exe")
        has_psexec = os.path.exists(psexec_path)

        try:
            # 纯自身引擎
            if engine == "native" or (engine == "auto" and not has_psexec):
                r = subprocess.run(("taskkill", "/F", "/PID", str(pid), "/T"), creationflags=CREATE_NO_WINDOW, capture_output=True)
                return r.returncode == 0
                
            # 第三方引擎
            if engine in ("psexec", "auto") and has_psexec:
                subprocess.run(
                    (psexec_path, "-accepteula", "-s", "-d", "taskkill", "/F", "/PID", str(pid), "/T"),
                    creationflags=CREATE_NO_WINDOW, capture_output=True
                )
                time.sleep(1)
                # 检查是否真的死了
                check = subprocess.run(("tasklist", "/FI", f"PID eq {pid}", "/NH"), creationflags=CREATE_NO_WINDOW, capture_output=True, text=True)
                return str(pid) not in check.stdout
        except Exception:
            pass
        return False


class MainWindow:
    def __init__(self, root, config):
        self._root = root
        self._cfg = config
        self._security = SecurityChecker()
        self._executor = PowerShellExecutor(self.log)
        self._overlay = None
        self._log_file = None
        self._log_lock = threading.Lock()
        self._closing = False
        
        self._topmost_job = None
        self._current_layout = None
        self._progress_job = None
        self._simulated_progress = 0

        self._target_hwnd = 0
        self._target_pid = 0
        self._target_name = ""
        self._target_path = ""

        self._move_src = ""
        self._move_dst = ""

        self._mode = tk.StringVar(value=config.mode)
        
        self._build_window()
        self._build_styles()
        
        self._theme_mgr = ThemeManager(self._root, self._cfg.theme_color)
        
        self._build_ui()
        self._build_context_menu()
        self._bind_shortcuts()
        self._init_log_file()
        self._apply_mode()
        self._refresh_tree()

        self._root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self._root.after(100, self._apply_window_features)
        
        if self._cfg.force_topmost:
            self._enable_absolute_topmost()

    def _build_window(self):
        self._root.title(f"{APP_TITLE} v{APP_VERSION}")
        self._root.geometry(self._cfg.geometry)
        self._root.configure(bg="#1E1E1E")
        self._style_frame = tk.Frame(self._root, bg="#1E1E1E")
        self._style_frame.pack(fill=tk.BOTH, expand=True)

    def _build_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#252526", foreground="#CCCCCC", fieldbackground="#252526", rowheight=24, font=("Consolas", 10), borderwidth=0)
        style.map("Treeview", background=[('selected', '#094771')], foreground=[('selected', '#FFFFFF')])
        style.configure("TScrollbar", gripcount=0, background="#2D2D2D", darkcolor="#1E1E1E", lightcolor="#1E1E1E", troughcolor="#1E1E1E", bordercolor="#1E1E1E", arrowcolor="#1E1E1E")

    def _build_ui(self):
        if self._current_layout:
            for widget in self._current_layout.winfo_children():
                widget.destroy()
            self._current_layout.destroy()

        self._current_layout = tk.Frame(self._style_frame, bg="#1E1E1E")
        self._current_layout.pack(fill=tk.BOTH, expand=True)
        self._theme_mgr.register_bg(self._current_layout)

        self._paned = ttk.PanedWindow(self._current_layout, orient=tk.HORIZONTAL)
        self._paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._left = tk.Frame(self._paned, bg="#1E1E1E")
        self._theme_mgr.register_bg(self._left)
        
        p_top = 8 if self._cfg.ui_style == "win11" else 2

        self._build_top_bar(self._left, p_top)
        self._build_monitor(self._left, p_top)
        self._build_progress_bar(self._left)
        self._build_toolbar(self._left)
        self._build_drop(self._left, p_top)
        self._build_tree(self._left)

        self._right = tk.Frame(self._paned, bg="#1E1E1E")
        self._theme_mgr.register_bg(self._right)
        self._build_log_panel(self._right)

        self._paned.add(self._left, weight=6)
        self._paned.add(self._right, weight=4)
        self._theme_mgr._apply_color(self._cfg.theme_color)

    def _build_top_bar(self, parent, pady):
        bar = tk.Frame(parent, bg="#1E1E1E")
        bar.pack(fill=tk.X, side=tk.TOP, pady=pady)
        self._theme_mgr.register_bg(bar)
        
        for text, val in (("操作模式：获取权限并打开", "grant_open"), ("操作模式：强制移动", "force_move")):
            rb = tk.Radiobutton(bar, text=text, variable=self._mode, value=val, command=self._apply_mode, bg="#1E1E1E", fg="#FFFFFF", selectcolor="#094771", activebackground="#1E1E1E", activeforeground="#FFFFFF", font=("微软雅黑", 9))
            rb.pack(side=tk.LEFT, padx=5)
            self._theme_mgr.register_bg(rb)
            self._theme_mgr.register(rb, is_primary=False)

        self._btn_settings = RoundedButton(bar, text="设置", command=self._open_settings, bg="#3C3C3C", hover_bg="#505050", press_bg="#2A2A2A", height=28, width=60)
        self._btn_settings.pack(side=tk.RIGHT, padx=10)
        self._theme_mgr.register(self._btn_settings, is_primary=False)

    def _build_monitor(self, parent, pady):
        f = tk.LabelFrame(parent, text="进程监视器", bg="#1E1E1E", fg="#FF3333", font=("微软雅黑", 10, "bold"))
        f.pack(fill=tk.X, padx=10, pady=pady)
        self._theme_mgr.register_bg(f)
        
        self._btn_capture = RoundedButton(f, text="选择目标窗口", command=self._on_select_window, height=30, width=120)
        self._btn_capture.pack(side=tk.LEFT, padx=5, pady=5)
        self._theme_mgr.register(self._btn_capture, is_primary=True)
        
        self._btn_kill = RoundedButton(f, text="强制终止目标进程", command=self._on_kill, fg="#FF4444", height=30, width=140)
        self._btn_kill.pack(side=tk.LEFT, padx=5, pady=5)
        self._btn_kill.config_state(tk.DISABLED)
        self._theme_mgr.register(self._btn_kill, is_primary=False)

        self._btn_taskmgr = RoundedButton(f, text="任务管理器", command=self._open_task_mgr, height=30, width=100)
        self._btn_taskmgr.pack(side=tk.LEFT, padx=5, pady=5)
        self._theme_mgr.register(self._btn_taskmgr, is_primary=False)
        
        self._lbl_target = tk.Label(f, text="未选定目标进程", fg="#888888", bg="#1E1E1E")
        self._lbl_target.pack(side=tk.LEFT, padx=10)
        self._theme_mgr.register_bg(self._lbl_target)

    def _build_progress_bar(self, parent):
        self._progress_bar = CanvasProgressBar(parent)
        self._progress_bar.pack(fill=tk.X, padx=15, pady=(0, 5))
        self._theme_mgr.register_progress(self._progress_bar)
        self._progress_bar.set(0, "就绪")

    def _build_toolbar(self, parent):
        bar = tk.Frame(parent, bg="#2D2D2D")
        bar.pack(fill=tk.X, side=tk.TOP, pady=5)
        
        self._btn_refresh = RoundedButton(bar, text="刷新目录", command=self._refresh_tree, height=26, width=80)
        self._btn_refresh.pack(side=tk.LEFT, padx=5)
        self._theme_mgr.register(self._btn_refresh, is_primary=False)
        
        lbl = tk.Label(bar, text="支持拖拽文件/文件夹至下方区域", fg="#888888", bg="#2D2D2D", font=("微软雅黑", 9))
        lbl.pack(side=tk.LEFT, padx=10)
        self._theme_mgr.register_bg(lbl)

    def _build_drop(self, parent, pady):
        self._drop = tk.Frame(parent, bg="#1E1E1E")
        self._drop.pack(fill=tk.X, padx=5, pady=pady)
        self._theme_mgr.register_bg(self._drop)

        self._lbl_single = tk.Label(self._drop, text="[文件拖放区]\n请将文件或文件夹拖入此处\n系统将执行提权处理", bg="#2D2D2D", fg="#00FF00", font=("微软雅黑", 11, "bold"), relief="ridge", bd=1, pady=15 if self._cfg.ui_style == "win11" else 5)
        self._lbl_single.drop_target_register(DND_FILES)
        self._lbl_single.dnd_bind('<<Drop>>', self._on_drop_single)
        self._theme_mgr.register_bg(self._lbl_single)

        self._frame_double = tk.Frame(self._drop, bg="#1E1E1E")
        self._theme_mgr.register_bg(self._frame_double)
        
        self._lbl_src = tk.Label(self._frame_double, text="[源路径]\n待移动的文件/文件夹", bg="#2D2D2D", fg="#00FF00", font=("微软雅黑", 10, "bold"), relief="ridge", bd=1, pady=10 if self._cfg.ui_style == "win11" else 4)
        self._lbl_src.pack(fill=tk.X, pady=(0, 5))
        self._lbl_src.drop_target_register(DND_FILES)
        self._lbl_src.dnd_bind('<<Drop>>', self._on_drop_source)
        self._theme_mgr.register_bg(self._lbl_src)

        self._lbl_dst = tk.Label(self._frame_double, text="[目标路径]\n目标文件夹\n(均就绪后自动执行)", bg="#2D2D2D", fg="#FFAA00", font=("微软雅黑", 10, "bold"), relief="ridge", bd=1, pady=10 if self._cfg.ui_style == "win11" else 4)
        self._lbl_dst.pack(fill=tk.X, pady=(5, 0))
        self._lbl_dst.drop_target_register(DND_FILES)
        self._lbl_dst.dnd_bind('<<Drop>>', self._on_drop_dest)
        self._theme_mgr.register_bg(self._lbl_dst)

    def _build_tree(self, parent):
        self._tree = ttk.Treeview(parent, selectmode="browse", show="tree")
        self._tree.pack(fill=tk.BOTH, expand=True)
        self._tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self._tree.bind("<Button-3>", self._on_tree_right_click)

    def _build_log_panel(self, parent):
        bar = tk.Frame(parent, bg="#2D2D2D")
        bar.pack(fill=tk.X, side=tk.TOP)
        self._theme_mgr.register_bg(bar)
        
        lbl = tk.Label(bar, text="系统操作日志", font=("微软雅黑", 9, "bold"), fg="#CCCCCC", bg="#2D2D2D")
        lbl.pack(side=tk.LEFT, padx=5, pady=5)
        self._theme_mgr.register_bg(lbl)
        
        self._btn_clear = RoundedButton(bar, text="清空记录", command=self._clear_log, height=24, width=70)
        self._btn_clear.pack(side=tk.RIGHT, padx=5, pady=5)
        self._theme_mgr.register(self._btn_clear, is_primary=False)
        
        self._log_area = tk.Text(parent, state=tk.DISABLED, bg="#1E1E1E", fg="#00FF00", font=("Consolas", 9), borderwidth=0, highlightthickness=0, wrap=tk.WORD, yscrollcommand=lambda *a: None)
        self._log_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._log_area.bind("<MouseWheel>", self._on_log_scroll)
        self._theme_mgr.register_bg(self._log_area)

    def _on_log_scroll(self, event):
        self._log_area.yview_scroll(int(-1*(event.delta/120)), "units")

    def _apply_window_features(self):
        try:
            hwnd = int(self._root.wm_frame(), 16)
            set_window_rounded(hwnd, self._cfg.window_rounded)
        except Exception: pass

    def _open_settings(self):
        SettingsWindow(self._root, self._cfg, self._theme_mgr, self._on_settings_saved)

    def _on_settings_saved(self):
        save_config(self._cfg)
        self._build_ui() 
        self._apply_window_features()
        if self._cfg.force_topmost: self._enable_absolute_topmost()
        else: self._disable_absolute_topmost()

    def _enable_absolute_topmost(self):
        self._root.attributes('-topmost', True)
        self._force_topmost_loop()

    def _force_topmost_loop(self):
        if not self._cfg.force_topmost or self._closing: return
        try:
            hwnd = int(self._root.wm_frame(), 16)
            set_absolute_topmost(hwnd)
        except Exception: pass
        self._topmost_job = self._root.after(100, self._force_topmost_loop)

    def _disable_absolute_topmost(self):
        self._root.attributes('-topmost', False)
        if self._topmost_job:
            self._root.after_cancel(self._topmost_job)
            self._topmost_job = None

    def _build_context_menu(self):
        self._menu = tk.Menu(self._root, tearoff=0, bg="#2D2D2D", fg="#F0F0F0", activebackground="#094771", activeforeground="#FFFFFF")
        self._menu.add_command(label="打开", command=self._action_open)
        self._menu.add_command(label="获取权限并打开", command=self._action_grant)
        self._menu.add_separator()
        self._menu.add_command(label="属性", command=self._action_properties)

    def _bind_shortcuts(self):
        self._root.bind("<F5>", lambda e: self._refresh_tree())
        self._root.bind("<Control-l>", lambda e: self._clear_log())

    def _init_log_file(self):
        if not self._cfg.log_to_file: return
        try:
            d = get_log_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"tim_{time.strftime('%Y%m%d')}.log"
            self._log_file = open(path, "a", encoding="utf-8")
        except OSError: self._log_file = None

    def log(self, text):
        if self._closing: return
        line = f"[{time.strftime('%H:%M:%S')}] {text}\n"
        if self._log_file:
            with self._log_lock:
                try:
                    self._log_file.write(line)
                    self._log_file.flush()
                except OSError: pass
        def _ui():
            try:
                if self._log_area.winfo_exists():
                    self._log_area.config(state=tk.NORMAL)
                    self._log_area.insert(tk.END, line)
                    self._log_area.see(tk.END)
                    self._log_area.config(state=tk.DISABLED)
            except tk.TclError: pass
        try: self._root.after(0, _ui)
        except tk.TclError: pass

    def _clear_log(self):
        self._log_area.config(state=tk.NORMAL)
        self._log_area.delete(1.0, tk.END)
        self._log_area.config(state=tk.DISABLED)

    def _apply_mode(self):
        if self._mode.get() == "grant_open":
            self._frame_double.pack_forget()
            self._lbl_single.pack(fill=tk.X)
        else:
            self._lbl_single.pack_forget()
            self._frame_double.pack(fill=tk.X)

    def _start_progress(self, text):
        self._progress_bar.set(0, text)
        self._simulated_progress = 0
        self._update_progress_loop()

    def _update_progress_loop(self):
        if self._simulated_progress < 95:
            self._simulated_progress += 1.5
            self._progress_bar.set(self._simulated_progress)
            self._progress_job = self._root.after(100, self._update_progress_loop)

    def _stop_progress(self, success=True):
        if self._progress_job:
            self._root.after_cancel(self._progress_job)
            self._progress_job = None
        if success:
            self._progress_bar.set(100, "执行完成")
            self._root.after(2000, lambda: self._progress_bar.set(0, "就绪"))
        else:
            self._progress_bar.set(0, "执行失败/中断")
            self._root.after(3000, lambda: self._progress_bar.set(0, "就绪"))

    def _on_select_window(self):
        if self._overlay is not None: return
        self._overlay = WindowCaptureOverlay(self._root, on_target=self._on_target_selected, on_log=self.log, on_close=self._on_overlay_closed)

    def _on_overlay_closed(self):
        self._overlay = None
        try:
            self._root.deiconify()
            if self._cfg.force_topmost: self._enable_absolute_topmost()
        except tk.TclError: pass

    def _on_target_selected(self, hwnd, pid, path, cls, title):
        self._target_hwnd = hwnd
        self._target_pid = pid
        self._target_path = path
        self._target_name = os.path.basename(path)
        self._lbl_target.config(text=f"目标进程: {self._target_name} (PID: {pid})", fg="#FF4444")
        self._btn_kill.config_state(tk.NORMAL)
        self.log("---------- 窗口捕获成功 ----------")
        self.log(f"窗口标题: {title}")
        self.log(f"窗口句柄: 0x{hwnd:08X}")
        self.log(f"窗口类名: {cls}")
        self.log(f"进程名称: {self._target_name}")
        self.log(f"进程 PID: {pid}")
        self.log(f"文件路径: {path}")
        self.log("----------------------------------")

    def _open_task_mgr(self):
        TaskManagerWindow(self._root, self._cfg, self.log)

    def _on_kill(self):
        if not self._target_pid: return
        if self._cfg.confirm_before_kill:
            if not messagebox.askyesno("确认", f"强制终止进程 {self._target_name} (PID: {self._target_pid})？\n所选引擎: {self._cfg.kill_engine}"): return
        if not is_window(self._target_hwnd):
            self.log(f"目标窗口已关闭，进程 {self._target_name} 不再响应。")
            self._btn_kill.config_state(tk.DISABLED)
            return
        threading.Thread(target=self._do_kill, daemon=True).start()

    def _do_kill(self):
        self._root.after(0, lambda: self._start_progress("准备终止进程..."))
        try:
            engine = self._cfg.kill_engine
            psexec_path = os.path.join(get_base_path(), "Coreengine", "PSTools", "PsExec64.exe")
            has_psexec = os.path.exists(psexec_path)
            
            if engine == "auto":
                self.log("引擎策略：自动模式 (优先自身，失败降级第三方)")
            elif engine == "native":
                self.log("引擎策略：纯自身引擎 (taskkill)")
            else:
                self.log("引擎策略：第三方引擎 (PsExec)")

            success = False
            err_msg = ""

            # 1. 纯自身引擎 (或者自动模式先试自身)
            if engine in ("native", "auto"):
                self._root.after(0, lambda: self._progress_bar.set(20, "执行自身引擎强杀..."))
                self.log("正在执行自身引擎...")
                ps_script = f"""
if (taskkill /F /PID {self._target_pid} /T 2>&1 | Out-Null) {{
    Write-Output "KILL_SUCCESS"
}} else {{
    Write-Output "KILL_FAILED"
    taskkill /F /PID {self._target_pid} /T
}}
"""
                out = self._executor.execute(ps_script)
                if "KILL_SUCCESS" in out:
                    success = True
                    self.log("自身引擎强杀成功。")
                else:
                    err_msg = out
                    self.log("自身引擎强杀失败。")
                    if engine == "auto":
                        self.log("尝试降级调用第三方引擎...")

            # 2. 第三方引擎
            if not success and engine in ("psexec", "auto"):
                if not has_psexec:
                    self.log("[警告] 未找到第三方引擎 (PsExec64.exe)，文件可能缺失。")
                    if engine == "psexec":
                        self.log("[失败] 强制使用第三方引擎，但文件不存在。")
                        self._root.after(0, lambda: self._stop_progress(False))
                        return
                else:
                    self._root.after(0, lambda: self._progress_bar.set(60, "执行第三方引擎强杀..."))
                    self.log("正在调用第三方引擎 (PsExec SYSTEM 权限)...")
                    # 第三方引擎直接在 Python 层调，比走 PowerShell 更稳
                    subprocess.run(
                        (psexec_path, "-accepteula", "-s", "-d", "taskkill", "/F", "/PID", str(self._target_pid), "/T"),
                        creationflags=CREATE_NO_WINDOW, capture_output=True
                    )
                    time.sleep(1) # 给系统反应时间
                    check = subprocess.run(("tasklist", "/FI", f"PID eq {self._target_pid}", "/NH"), creationflags=CREATE_NO_WINDOW, capture_output=True, text=True)
                    if str(self._target_pid) not in check.stdout:
                        success = True
                        self.log("第三方引擎强杀成功。")
                    else:
                        self.log("第三方引擎强杀失败，可能受 PPL 保护。")

            if success:
                self.log(f"进程 {self._target_name} (PID: {self._target_pid}) 终止成功。")
                try: self._root.after(0, lambda: self._btn_kill.config_state(tk.DISABLED))
                except tk.TclError: pass
                self._root.after(0, lambda: self._stop_progress(True))
            else:
                self.log("终止失败，详细输出：")
                for line in err_msg.splitlines():
                    if line.strip() and "KILL_FAILED" not in line:
                        self.log(line.strip())
                self.log("提示：若提示拒绝访问，说明该进程属于系统核心（受 PPL 等驱动保护），或引擎被拦截。")
                self._root.after(0, lambda: self._stop_progress(False))
                
        except Exception as e:
            self.log(f"终止异常：{e}")
            self._root.after(0, lambda: self._stop_progress(False))

    def _on_drop_single(self, event):
        raw = self._root.tk.splitlist(event.data)
        if not raw: return
        paths = [os.path.normpath(p) for p in raw]
        self._root.after(10, self._process_drop_single, paths)

    def _process_drop_single(self, paths):
        accepted = []
        for p in paths:
            if self._security.is_protected(p): self.log(f"[拒绝] {p} 位于系统核心保护区。")
            else: accepted.append(p)
        if not accepted: return
        if not messagebox.askyesno("确认", f"将对 {len(accepted)} 个目标执行提权处理，是否继续？"): return
        self._start_progress("正在处理提权...")
        threading.Thread(target=self._run_grant, args=(accepted,), daemon=True).start()

    def _run_grant(self, accepted):
        ok = 0
        for p in accepted:
            try:
                grant_and_open(self._executor, p, self.log)
                ok += 1
            except Exception as e:
                self.log(f"[跳过] {p}: {e}")
        self.log(f"批量完成：成功 {ok}/{len(accepted)}。")
        self._root.after(0, lambda: self._stop_progress(True))

    def _on_drop_source(self, event):
        self._move_src = os.path.normpath(self._root.tk.splitlist(event.data)[0])
        self._lbl_src.config(text=f"[源路径]\n{self._move_src}", fg="#FFFFFF")
        self._check_move_ready()

    def _on_drop_dest(self, event):
        self._move_dst = os.path.normpath(self._root.tk.splitlist(event.data)[0])
        self._lbl_dst.config(text=f"[目标路径]\n{self._move_dst}", fg="#FFFFFF")
        self._check_move_ready()

    def _check_move_ready(self):
        if not (self._move_src and self._move_dst): return
        src, dst = self._move_src, self._move_dst
        self._move_src = self._move_dst = ""
        self._lbl_src.config(text="[源路径]\n待移动的文件/文件夹", fg="#00FF00")
        self._lbl_dst.config(text="[目标路径]\n目标文件夹\n(均就绪后自动执行)", fg="#FFAA00")

        err = validate_move(src, dst)
        if err:
            self.log(f"[拒绝] 移动参数无效：{err}")
            messagebox.showerror("参数错误", err)
            return
        if self._security.is_protected(src):
            self.log(f"[拒绝] 源路径位于系统核心保护区：{src}")
            messagebox.showerror("拒绝", "源路径位于系统核心保护区。")
            return
        if not messagebox.askyesno("确认", f"将移动：\n{src}\n→ {dst}\n\n过程中会重启 explorer，是否继续？"): return
        
        self._start_progress("正在强制移动文件...")
        threading.Thread(target=self._run_move, args=(src, dst), daemon=True).start()

    def _run_move(self, src, dst):
        try:
            force_move(self._executor, src, dst, self.log)
            self._root.after(0, lambda: self._stop_progress(True))
        except Exception as e:
            self.log(f"移动异常: {e}")
            self._root.after(0, lambda: self._stop_progress(False))

    def _refresh_tree(self):
        for item in self._tree.get_children(): self._tree.delete(item)
        for d in string.ascii_uppercase:
            if os.path.exists(f"{d}:\\"):
                node = self._tree.insert("", "end", text=f"[磁盘] {d}:\\", values=(f"{d}:\\", "FS"))
                self._tree.insert(node, "end", text="加载中...", values=("", "DUMMY"))

    def _on_tree_open(self, event):
        node = self._tree.focus()
        if not node: return
        children = self._tree.get_children(node)
        if len(children) == 1 and self._tree.item(children[0], "values")[1] == "DUMMY":
            self._tree.delete(children[0])
            path, _ = self._tree.item(node, "values")
            threading.Thread(target=self._load_children, args=(node, path), daemon=True).start()

    def _load_children(self, parent, path):
        try:
            items = []
            for name in os.listdir(path):
                full = os.path.join(path, name)
                try: items.append((name, full, os.path.isdir(full)))
                except OSError: continue
        except OSError: items = []

        def _ui():
            if self._closing: return
            try:
                for name, full, is_dir in items:
                    prefix = "[目录]" if is_dir else "[文件]"
                    node = self._tree.insert(parent, "end", text=f"{prefix} {name}", values=(full, "DIR" if is_dir else "FILE"))
                    if is_dir: self._tree.insert(node, "end", text="加载中...", values=("", "DUMMY"))
            except tk.TclError: pass
        try: self._root.after(0, _ui)
        except tk.TclError: pass

    def _on_tree_right_click(self, event):
        item = self._tree.identify_row(event.y)
        if not item: return
        self._tree.selection_set(item)
        self._tree.focus(item)
        self._menu.post(event.x_root, event.y_root)

    def _get_selected_path(self):
        item = self._tree.focus()
        if not item: return None
        path, node_type = self._tree.item(item, "values")
        if node_type == "DUMMY" or not path: return None
        return path

    def _action_open(self):
        path = self._get_selected_path()
        if not path: return
        try: os.startfile(path)
        except OSError as e: self.log(f"打开失败：{e}")

    def _action_grant(self):
        path = self._get_selected_path()
        if not path: return
        if self._security.is_protected(path):
            self.log(f"[拒绝] {path} 位于系统核心保护区。")
            return
        self._start_progress("正在处理提权...")
        threading.Thread(target=self._run_grant, args=([path],), daemon=True).start()

    def _action_properties(self):
        path = self._get_selected_path()
        if not path: return
        try:
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(None, "properties", path, None, None, 1)
        except Exception as e: self.log(f"显示属性失败：{e}")

    def on_closing(self):
        self._closing = True
        try:
            self._cfg.geometry = self._root.geometry()
            self._cfg.mode = self._mode.get()
            save_config(self._cfg)
        except Exception: pass
        if self._log_file:
            try: self._log_file.close()
            except OSError: pass
        self._executor.cleanup()
        self._root.destroy()

__all__ = ["MainWindow"]