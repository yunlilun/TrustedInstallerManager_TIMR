import ctypes
import os
import tempfile
import threading
import time
from typing import Callable

from config import get_dangerous_paths

CREATE_NO_WINDOW = 0x08000000
DONE_SENTINEL = "__TIM_DONE__"
EXEC_TIMEOUT = 60.0        # 延长至 60 秒，大文件夹 takeown 需要时间
POLL_INTERVAL = 0.3

LogCb = Callable[[str], None]


class SecurityChecker:
    def __init__(self) -> None:
        self._dangerous = get_dangerous_paths()

    def is_protected(self, path: str) -> bool:
        if not path:
            return True
        abs_path = os.path.normpath(os.path.abspath(path)).lower()
        for dp in self._dangerous:
            if abs_path == dp or abs_path.startswith(dp + os.sep):
                return True
        return False


class PowerShellExecutor:
    def __init__(self, log_cb: LogCb) -> None:
        self._log = log_cb
        self._temp_files: list[str] = []
        self._lock = threading.Lock()

    def _new_temp(self, suffix: str) -> str:
        f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="w", encoding="utf-8-sig")
        f.close()
        with self._lock:
            self._temp_files.append(f.name)
        return f.name

    def _register(self, path: str) -> None:
        with self._lock:
            self._temp_files.append(path)

    def cleanup(self) -> None:
        with self._lock:
            files, self._temp_files = self._temp_files, []
        for p in files:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    def execute(self, script_body: str) -> str:
        ps1 = self._new_temp(".ps1")
        log = ps1 + ".log"
        self._register(log)
        
        try:
            with open(ps1, "w", encoding="utf-8-sig") as f:
                # 强制控制台和 PowerShell 使用 UTF-8 编码，彻底解决中文乱码
                f.write("chcp 65001 | Out-Null\n")
                f.write("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n")
                f.write("$OutputEncoding = [System.Text.Encoding]::UTF8\n")
                # 静默错误，防止 takeown/icacls 输出海量报错刷屏
                f.write("$ErrorActionPreference = 'SilentlyContinue'\n")
                f.write(script_body)
                f.write(f"\nWrite-Output '{DONE_SENTINEL}'\n")
                
            args = f'-NoProfile -ExecutionPolicy Bypass -Command "& \'{ps1}\' *> \'{log}\'"'
            
            self._log("请求 UAC 提权...")
            # 这一步会弹出 UAC 框，不会产生黑框
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", args, None, 0)
            
            if ret <= 32:
                return f"[失败] 提权被拦截或取消 (错误码: {ret})。"
            
            self._log("UAC 已确认，正在后台执行，请稍候...")
            return self._wait_for_log(log)
            
        except Exception as e:
            return f"[异常] {e}"

    def _wait_for_log(self, log: str) -> str:
        start = time.time()
        last_report = start
        
        while time.time() - start < EXEC_TIMEOUT:
            if os.path.exists(log):
                try:
                    # 尝试多种编码，防止底层命令输出 GBK 或产生非法字节流
                    content = ""
                    for enc in ("utf-8", "gbk", "utf-16"):
                        try:
                            with open(log, "r", encoding=enc) as f:
                                content = f.read()
                            break
                        except UnicodeDecodeError:
                            continue
                    
                    if DONE_SENTINEL in content:
                        clean_content = content.replace(DONE_SENTINEL, "").rstrip()
                        # 过滤掉无法识别的控制字符乱码
                        clean_content = "".join(c for c in clean_content if c == '\n' or c == '\r' or ord(c) >= 32)
                        return clean_content
                except Exception:
                    pass
            
            now = time.time()
            if now - last_report > 5.0:
                self._log(f"正在执行中，已等待 {int(now - start)} 秒... (处理大文件夹可能较慢)")
                last_report = now
                
            time.sleep(POLL_INTERVAL)
            
        try:
            if os.path.exists(log):
                return "[超时] 脚本执行时间过长，可能被安全软件拦截，或文件夹内文件过多。"
        except OSError:
            pass
        return "[超时] 未获取到执行结果。"


def _ps_quote(path: str) -> str:
    return "'" + path.replace("'", "''").replace("`", "``") + "'"


def grant_and_open(executor: PowerShellExecutor, path: str, log: LogCb) -> None:
    ps = f"""
$path = {_ps_quote(path)}
if (Test-Path -LiteralPath $path) {{
    attrib -r -s -h "$path" /s /d *> $null
    takeown /f "$path" /r /d Y *> $null
    icacls "$path" /grant *S-1-5-32-544:F /t /c /q *> $null
    Write-Output "权限修改完成。"
}} else {{
    Write-Output "路径不存在。"
}}
"""
    log(f"处理: {path}")
    out = executor.execute(ps)
    for line in out.splitlines():
        if line.strip():
            log(line.strip())
    try:
        os.startfile(path)
        log("已调用系统默认程序打开。")
    except OSError as e:
        log(f"打开失败: {e}")


def grant_and_open_many(executor: PowerShellExecutor, paths: list[str], log: LogCb) -> None:
    ok = 0
    for p in paths:
        try:
            grant_and_open(executor, p, log)
            ok += 1
        except Exception as e:
            log(f"[跳过] {p}: {e}")
    log(f"批量完成：成功 {ok}/{len(paths)}。")


def validate_move(src: str, dst: str) -> str | None:
    if not os.path.exists(src):
        return "源路径不存在。"
    if not os.path.exists(dst):
        return "目标路径不存在。"
    if not os.path.isdir(dst):
        return "目标路径不是文件夹。"
    s = os.path.normpath(os.path.abspath(src))
    d = os.path.normpath(os.path.abspath(dst))
    if s == d:
        return "源路径与目标路径相同。"
    if d.startswith(s + os.sep):
        return "目标位于源内部，会形成自嵌套。"
    if os.path.splitdrive(s)[0].lower() != os.path.splitdrive(d)[0].lower():
        return None
    final = os.path.join(d, os.path.basename(s))
    if os.path.exists(final):
        return f"目标已存在同名项：{final}"
    return None


def force_move(executor: PowerShellExecutor, src: str, dst: str, log: LogCb) -> None:
    log(f"准备移动: {src} -> {dst}")
    ps = f"""
$src = {_ps_quote(src)}
$dst = {_ps_quote(dst)}
if (-not (Test-Path -LiteralPath $src)) {{ Write-Output "源不存在"; return }}
attrib -r -s -h "$src" /s /d *> $null
takeown /f "$src" /r /d Y *> $null
icacls "$src" /grant *S-1-5-32-544:F /t /c /q *> $null
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$moved = $false
try {{
    Move-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop
    Write-Output "SUCCESS_MOVE"
    $moved = $true
}} catch {{
    Write-Output "Move-Item 失败，尝试内核强移..."
}}
if (-not $moved) {{
    try {{
        if (-not ("NativeMethods" -as [type])) {{
            Add-Type -Namespace '' -Name 'NativeMethods' -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet=System.Runtime.InteropServices.CharSet.Unicode, SetLastError=true)]
public static extern bool MoveFileEx(string lpExistingFileName, string lpNewFileName, int dwFlags);
"@
        }}
        $finalDst = Join-Path $dst (Split-Path $src -Leaf)
        $ok = [NativeMethods]::MoveFileEx($src, $finalDst, 4)
        if ($ok) {{ Write-Output "SUCCESS_REBOOT" }} else {{ Write-Output "MoveFileEx 失败" }}
    }} catch {{
        Write-Output "P/Invoke 异常"
    }}
}}
Start-Process explorer
"""
    out = executor.execute(ps)
    for line in out.splitlines():
        if line.strip():
            log(line.strip())


__all__ = [
    "SecurityChecker", "PowerShellExecutor",
    "grant_and_open", "grant_and_open_many",
    "validate_move", "force_move",
]