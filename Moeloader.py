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
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

VERSION = "v1.0-patch1"
CREATOR = "liversoda.wx on discord"
GITHUB_SELF = "https://api.github.com/repos/ServalSoftworks/moeloader/releases/latest"
BEPINEX_API = "https://api.github.com/repos/BepInEx/BepInEx/releases/latest"

# Preferred BepInEx package
DEFAULT_BEPINEX_ASSET = "BepInEx_win_x64"

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

    # Find the .exe asset
    asset_url = None
    for a in data.get("assets", []):
        if a["name"].lower().endswith(".exe"):
            asset_url = a["browser_download_url"]
            break

    if not asset_url:
        print("[!] No .exe found in the latest release assets.")
        return

    print(f"[*] Newer version available ({remote_tag}). Downloading...")

    # Current running executable (works for both .py and frozen .exe)
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

    # Create helper batch file next to the EXE
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

    # Launch the batch and exit immediately
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
        else:
            print(f"[!] Unknown command: {cmd}. Type 'help' for list.")

if __name__ == "__main__":
    main()