#!/usr/bin/env python3
# Moeloader v1.0-patch1
# Creator: liversoda.wx on discord

import os
import sys
import zipfile
import shutil
import tempfile
import json
import re
import subprocess
import ctypes
import ctypes.wintypes as wintypes
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from datetime import datetime

VERSION = "v1.1-patch1"
CREATOR = "liversoda.wx on discord"
GITHUB_SELF = "https://api.github.com/repos/ServalSoftworks/moeloader/releases/latest"
BEPINEX_API = "https://api.github.com/repos/BepInEx/BepInEx/releases/latest"

DEFAULT_BEPINEX_ASSET = "BepInEx_win_x64"

# ---------- Windows API helpers for jimmy ----------
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]

def get_process_info(pid: int):
    """Collect detailed process information for debugging."""
    info = {"pid": pid}

    # Open process
    h_process = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h_process:
        # Try limited access
        h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h_process:
            err = ctypes.get_last_error()
            return {"error": f"Failed to open process (error {err}). Try running as Administrator."}

    try:
        # Executable path
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h_process, 0, buf, ctypes.byref(size)):
            info["executable"] = buf.value
        else:
            info["executable"] = "<unavailable>"

        # Architecture
        is_wow64 = wintypes.BOOL()
        if kernel32.IsWow64Process(h_process, ctypes.byref(is_wow64)):
            info["architecture"] = "32-bit (WOW64)" if is_wow64.value else "64-bit"
        else:
            info["architecture"] = "unknown"

        # Memory info
        mem = PROCESS_MEMORY_COUNTERS()
        mem.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if psapi.GetProcessMemoryInfo(h_process, ctypes.byref(mem), mem.cb):
            info["working_set_mb"] = round(mem.WorkingSetSize / (1024 * 1024), 2)
            info["peak_working_set_mb"] = round(mem.PeakWorkingSetSize / (1024 * 1024), 2)
            info["pagefile_mb"] = round(mem.PagefileUsage / (1024 * 1024), 2)
        else:
            info["memory"] = "<unavailable>"

        # Process times (creation time)
        creation = wintypes.FILETIME()
        exit_t = wintypes.FILETIME()
        kernel_t = wintypes.FILETIME()
        user_t = wintypes.FILETIME()
        if kernel32.GetProcessTimes(h_process, ctypes.byref(creation), ctypes.byref(exit_t),
                                    ctypes.byref(kernel_t), ctypes.byref(user_t)):
            # Convert FILETIME to datetime
            timestamp = ((creation.dwHighDateTime << 32) + creation.dwLowDateTime) / 10_000_000 - 11644473600
            info["start_time"] = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            info["start_time"] = "<unavailable>"

    finally:
        kernel32.CloseHandle(h_process)

    # Extra info via PowerShell / tasklist (more reliable for name, cmdline, parent, etc.)
    try:
        # Process name + parent + memory via tasklist
        cmd = f'tasklist /fi "PID eq {pid}" /fo csv /nh /v'
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            # CSV: Image Name, PID, Session Name, Session#, Mem Usage, Status, User Name, CPU Time, Window Title
            parts = [p.strip('"') for p in result.stdout.strip().split('","')]
            if len(parts) >= 2:
                info["name"] = parts[0]
                if len(parts) >= 5:
                    info["memory_tasklist"] = parts[4]
                if len(parts) >= 7:
                    info["user"] = parts[6]
                if len(parts) >= 9:
                    info["window_title"] = parts[8]
    except Exception:
        pass

    # Command line via PowerShell (best source)
    try:
        ps = f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            info["command_line"] = result.stdout.strip()
    except Exception:
        info["command_line"] = "<unavailable>"

    # Parent PID
    try:
        ps = f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").ParentProcessId'
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip().isdigit():
            info["parent_pid"] = int(result.stdout.strip())
    except Exception:
        pass

    # Loaded modules (DLLs)
    try:
        h_process = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if h_process:
            modules = (wintypes.HMODULE * 1024)()
            needed = wintypes.DWORD()
            if psapi.EnumProcessModules(h_process, modules, ctypes.sizeof(modules), ctypes.byref(needed)):
                count = needed.value / ctypes.sizeof(wintypes.HMODULE)
                module_list = []
                for i in range(min(count, 50)):  # limit to first 50
                    mod_name = ctypes.create_unicode_buffer(260)
                    if psapi.GetModuleFileNameExW(h_process, modules[i], mod_name, 260):
                        module_list.append(mod_name.value)
                info["modules"] = module_list
                info["module_count"] = count
            kernel32.CloseHandle(h_process)
    except Exception:
        info["modules"] = "<unavailable>"

    return info

def cmd_jimmy(pid_str: str):
    try:
        pid = int(pid_str)
    except ValueError:
        print("[!] Invalid process ID. Usage: jimmy <process_id>")
        return

    print(f"[*] Attaching to process {pid}...")
    info = get_process_info(pid)

    if "error" in info:
        print(f"[!] {info['error']}")
        return

    print("\n========== Process Debug Info ==========")
    print(f"PID            : {info.get('pid')}")
    print(f"Name           : {info.get('name', '<unknown>')}")
    print(f"Parent PID     : {info.get('parent_pid', '<unknown>')}")
    print(f"Executable     : {info.get('executable', '<unknown>')}")
    print(f"Architecture   : {info.get('architecture', '<unknown>')}")
    print(f"Start Time     : {info.get('start_time', '<unknown>')}")
    print(f"User           : {info.get('user', '<unknown>')}")
    print(f"Window Title   : {info.get('window_title', '<none>')}")
    print(f"Working Set    : {info.get('working_set_mb', '?')} MB")
    print(f"Peak Working   : {info.get('peak_working_set_mb', '?')} MB")
    print(f"Pagefile       : {info.get('pagefile_mb', '?')} MB")
    print(f"Command Line   : {info.get('command_line', '<unavailable>')}")
    print(f"Module Count   : {info.get('module_count', '?')}")

    modules = info.get("modules")
    if isinstance(modules, list) and modules:
        print("\n--- Loaded Modules (first 50) ---")
        for m in modules:
            print(f"  {m}")
    print("========================================\n")

# ---------- Original commands ----------

def print_banner():
    print("=" * 50)
    print(f"  Moeloader {VERSION}")
    print("=" * 50)
    print()

def cmd_help():
    print("""
Available commands:
  help                          - Show this help
  ver                           - Show version and creator
  patch <game_directory>        - Install latest BepInEx into a Unity game folder
                                  (folder must contain UnityPlayer.dll and an .exe)
  put <bepinex_folder>          - Check BepInEx version and update if newer is available
  um                            - Check for a new version of Moeloader and update itself
  jimmy <process_id>            - Attach to a process and dump debug info
  exit / quit                   - Exit the program
""")

def cmd_ver():
    print(f"Moeloader {VERSION}")
    print(f"Creator: {CREATOR}")

def get_latest_bepinex_release():
    try:
        req = Request(BEPINEX_API, headers={"User-Agent": "Moeloader"})
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[!] Failed to fetch BepInEx release info: {e}")
        return None

def download_and_extract(url: str, target_dir: Path) -> bool:
    print(f"[*] Downloading: {url}")
    try:
        req = Request(url, headers={"User-Agent": "Moeloader"})
        with urlopen(req, timeout=60) as resp:
            data = resp.read()
    except Exception as e:
        print(f"[!] Download failed: {e}")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "bepinex.zip"
        zip_path.write_bytes(data)
        print("[*] Extracting...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target_dir)
    print("[+] Extraction complete.")
    return True

def cmd_patch(directory: str):
    game_dir = Path(directory).resolve()
    if not game_dir.is_dir():
        print(f"[!] Directory does not exist: {game_dir}")
        return

    unity_player = game_dir / "UnityPlayer.dll"
    exes = list(game_dir.glob("*.exe"))

    if not unity_player.exists():
        print("[!] UnityPlayer.dll not found. This does not look like a Unity game folder.")
        return
    if not exes:
        print("[!] No .exe file found in the directory.")
        return

    print(f"[*] Valid Unity game folder detected: {game_dir}")
    print(f"[*] Found executable(s): {[e.name for e in exes]}")

    release = get_latest_bepinex_release()
    if not release:
        return

    tag = release.get("tag_name", "unknown")
    print(f"[*] Latest BepInEx stable: {tag}")

    asset = None
    for a in release.get("assets", []):
        if DEFAULT_BEPINEX_ASSET in a["name"] and a["name"].endswith(".zip"):
            asset = a
            break
    if not asset:
        for a in release.get("assets", []):
            if "win" in a["name"].lower() and a["name"].endswith(".zip"):
                asset = a
                break

    if not asset:
        print("[!] Could not find a suitable Windows BepInEx package.")
        return

    print(f"[*] Using package: {asset['name']}")
    if download_and_extract(asset["browser_download_url"], game_dir):
        print(f"[+] BepInEx {tag} installed successfully into {game_dir}")
        print("[*] Run the game once so BepInEx can generate its config files.")
    else:
        print("[!] Installation failed.")

def get_installed_bepinex_version(bepinex_dir: Path) -> str | None:
    candidates = [
        bepinex_dir / "core" / "BepInEx.dll",
        bepinex_dir / "BepInEx" / "core" / "BepInEx.dll",
        bepinex_dir / "BepInEx.dll",
    ]
    for dll in candidates:
        if dll.exists():
            return "installed (version detection limited)"
    changelog = bepinex_dir / "changelog.txt"
    if changelog.exists():
        text = changelog.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?)", text)
        if m:
            return m.group(1)
    return None

def cmd_put(directory: str):
    bepinex_dir = Path(directory).resolve()
    if not bepinex_dir.is_dir():
        print(f"[!] Directory does not exist: {bepinex_dir}")
        return

    print(f"[*] Checking BepInEx installation at: {bepinex_dir}")
    current = get_installed_bepinex_version(bepinex_dir)
    if current:
        print(f"[*] Detected: {current}")
    else:
        print("[*] No clear BepInEx version markers found (will still update).")

    release = get_latest_bepinex_release()
    if not release:
        return

    latest_tag = release.get("tag_name", "").lstrip("v")
    print(f"[*] Latest available: {latest_tag}")

    print("[*] Updating BepInEx...")
    asset = None
    for a in release.get("assets", []):
        if DEFAULT_BEPINEX_ASSET in a["name"] and a["name"].endswith(".zip"):
            asset = a
            break
    if not asset:
        print("[!] Suitable package not found.")
        return

    if download_and_extract(asset["browser_download_url"], bepinex_dir):
        print(f"[+] BepInEx updated to {latest_tag}")
    else:
        print("[!] Update failed.")

def cmd_um():
    print("[*] Checking for Moeloader updates...")
    print(f"[*] Current version: {VERSION}")
    print(f"[*] Looking at: https://github.com/ServalSoftworks/moeloader")

    try:
        req = Request(GITHUB_SELF, headers={"User-Agent": "Moeloader"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except HTTPError as e:
        if e.code == 404:
            print("[!] Repository not found or has no releases yet.")
            return
        print(f"[!] GitHub API error: {e}")
        return
    except Exception as e:
        print(f"[!] Failed to check for updates: {e}")
        return

    remote_tag = data.get("tag_name", "").lstrip("v")
    print(f"[*] Remote version: {remote_tag}")

    if not remote_tag or remote_tag == VERSION.lstrip("v"):
        print("[+] You are already on the latest version.")
        return

    asset_url = None
    for a in data.get("assets", []):
        if a["name"].lower().endswith(".exe"):
            asset_url = a["browser_download_url"]
            break

    if not asset_url:
        print("[!] No .exe found in the latest release assets.")
        return

    print(f"[*] Newer version available ({remote_tag}). Downloading...")

    current_exe = Path(sys.executable).resolve()
    new_exe = current_exe.with_name(current_exe.stem + "_new.exe")

    try:
        req = Request(asset_url, headers={"User-Agent": "Moeloader"})
        with urlopen(req, timeout=90) as resp:
            new_exe.write_bytes(resp.read())
        print(f"[+] Downloaded new EXE to: {new_exe}")
    except Exception as e:
        print(f"[!] Download failed: {e}")
        return

    bat = current_exe.with_name("moeloader_update.bat")
    bat_content = f"""@echo off
echo Updating Moeloader...
timeout /t 2 /nobreak >nul
del /f /q "{current_exe}"
if exist "{current_exe}" (
    echo Failed to delete old EXE. Please close any running instances and try again.
    pause
    exit /b 1
)
ren "{new_exe}" "{current_exe.name}"
echo Update complete. Starting new version...
start "" "{current_exe}"
del "%~f0"
"""
    bat.write_text(bat_content, encoding="utf-8")
    print(f"[+] Update helper created: {bat}")
    print("[*] Closing now so the update can finish...")

    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS,
        close_fds=True
    )
    sys.exit(0)

def main():
    print_banner()
    print("Type 'help' for available commands.\n")

    while True:
        try:
            line = input("Moeloader> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not line:
            continue

        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("exit", "quit"):
            print("Bye.")
            break
        elif cmd == "help":
            cmd_help()
        elif cmd == "ver":
            cmd_ver()
        elif cmd == "patch":
            if not arg:
                print("[!] Usage: patch <directory>")
            else:
                cmd_patch(arg)
        elif cmd == "put":
            if not arg:
                print("[!] Usage: put <bepinex_folder>")
            else:
                cmd_put(arg)
        elif cmd == "um":
            cmd_um()
        elif cmd == "jimmy":
            if not arg:
                print("[!] Usage: jimmy <process_id>")
            else:
                cmd_jimmy(arg)
        else:
            print(f"[!] Unknown command: {cmd}. Type 'help' for list.")

if __name__ == "__main__":
    main()