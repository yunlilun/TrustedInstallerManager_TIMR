import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

APP_NAME = "TIMR"
APP_TITLE = "TIMR - TrustedInstaller 权限管理系统"
APP_VERSION = "1.5.2"

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
GA_ROOT = 2
GW_HWNDNEXT = 2
DWMWA_CLOAKED = 14

SKIP_WINDOW_CLASSES: frozenset[str] = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
})

MAG_RADIUS = 70
MAG_OFFSET = 120
MAG_CROP_SIZE = 50
CAPTURE_INTERVAL = 0.25

DEFAULT_THEMES = {
    "默认红": "#8B0000",
    "青绿": "#20B2AA",
    "橙色": "#FF8C00",
    "亮绿": "#32CD32",
    "天蓝": "#00BFFF",
    "深蓝": "#4169E1",
    "亮黄": "#FFD700",
    "紫色": "#8A2BE2"
}

def get_base_path() -> str:
    """
    获取程序运行的基础目录。
    如果是通过 PyInstaller 打包的单文件程序，返回临时解压目录 sys._MEIPASS；
    如果是源码运行，返回项目根目录。
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_dangerous_paths() -> list[str]:
    candidates = (
        os.environ.get("SystemRoot", r"C:\Windows"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramData", r"C:\ProgramData"),
    )
    return [os.path.normpath(p).lower() for p in candidates if p]

@dataclass
class AppConfig:
    mode: str = "grant_open"
    geometry: str = "1200x800"
    window_x: int = -1
    window_y: int = -1
    log_to_file: bool = True
    confirm_before_kill: bool = True
    theme_color: str = "#8B0000"
    force_topmost: bool = False
    enable_animation: bool = True
    ui_style: str = "win11"
    window_rounded: bool = True
    kill_engine: str = "auto"  # "auto" (优先第三方), "native" (纯自身), "psexec" (仅第三方)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

def get_config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME

def get_config_path() -> Path:
    return get_config_dir() / "config.json"

def get_log_dir() -> Path:
    return get_config_dir() / "logs"

def load_config() -> AppConfig:
    try:
        with open(get_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return AppConfig()
        return AppConfig.from_dict(data)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return AppConfig()

def save_config(cfg: AppConfig) -> bool:
    path = get_config_path()
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if tmp.exists(): tmp.unlink()
        except OSError: pass
        return False

__all__ = [
    "APP_NAME", "APP_TITLE", "APP_VERSION", "PW_RENDERFULLCONTENT", "SRCCOPY", "GA_ROOT", "GW_HWNDNEXT", "DWMWA_CLOAKED",
    "SKIP_WINDOW_CLASSES", "MAG_RADIUS", "MAG_OFFSET", "MAG_CROP_SIZE", "CAPTURE_INTERVAL", "DEFAULT_THEMES", 
    "get_dangerous_paths", "AppConfig", "load_config", "save_config", "get_config_dir", "get_config_path", "get_log_dir",
    "get_base_path"
]