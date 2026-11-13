import sys
import tkinter as tk
from tkinter import messagebox

from tkinterdnd2 import TkinterDnD

from config import load_config
from ui.main_window import MainWindow


class WarningWindow:
    # 常驻右上角红色警示浮窗；初始隐藏，由主窗口 Map/Unmap 事件同步显隐
    def __init__(self, master):
        self._top = tk.Toplevel(master)
        self._top.withdraw()
        self._top.overrideredirect(True)
        self._top.attributes('-topmost', True)
        self._top.configure(bg="#000000")
        tk.Label(
            self._top,
            text="[警告] 正在使用 TrustedInstaller 权限！\n数据无价，请谨慎操作 (不可逆)！",
            fg="#FF0000", bg="#000000",
            font=("微软雅黑", 12, "bold"), justify=tk.RIGHT,
            padx=15, pady=10,
        ).pack()
        self._top.update_idletasks()
        # 定位前需先计算实际宽度，否则屏幕宽度减去窗宽会得到错误偏移
        w = self._top.winfo_width()
        sw = self._top.winfo_screenwidth()
        self._top.geometry(f"+{sw - w - 20}+{20}")

    def show(self):
        try:
            self._top.deiconify()
        except tk.TclError:
            pass

    def hide(self):
        try:
            self._top.withdraw()
        except tk.TclError:
            pass


def _run():
    cfg = load_config()
    root = TkinterDnD.Tk()
    warning = WarningWindow(root)
    MainWindow(root, cfg)
    # 首次 Map 可能早于 bind 建立（MainWindow 构建期 root 已可见），末尾手动 show 一次兜底
    root.bind("<Map>", lambda e: warning.show())
    root.bind("<Unmap>", lambda e: warning.hide())
    warning.show()
    root.mainloop()


if __name__ == "__main__":
    try:
        _run()
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            messagebox.showerror("启动失败", traceback.format_exc(limit=3))
        except Exception:
            pass
        sys.exit(1)