"""The `paper-radar` command: run Paper Radar as a desktop app or as a local web app.

    paper-radar                   open the app in its own window (the default)
    paper-radar serve [--open]    run the local web server in this terminal
    paper-radar install-desktop   add Paper Radar to the app menu (Linux)
    paper-radar uninstall-desktop
    paper-radar paths             show where data, settings and logs live

The window is a Chrome/Chromium/Edge/Brave "app" window with its own profile, so it behaves like
a desktop app: its own taskbar entry, no tabs or address bar, and closing it stops the server.
Without one of those browsers the app opens in the default browser instead.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from paper_radar import __version__
from paper_radar.config import APP_DIR, APP_SCRIPT, ASSETS_DIR, env_file, user_config_dir, user_data_dir

DEFAULT_PORT = 8765
WINDOW_CLASS = "paper-radar"
LINUX_BROWSERS = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "microsoft-edge-stable", "brave-browser",
)
MAC_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
WINDOWS_BROWSERS = (
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
)


# --- Locations ------------------------------------------------------------------------------

def state_file() -> Path:
    return user_data_dir() / "server.json"


def log_file() -> Path:
    return user_data_dir() / "logs" / "server.log"


def browser_profile() -> Path:
    return user_data_dir() / "window-profile"


def applications_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"


def icon_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "icons" / "hicolor" / "scalable" / "apps" / "paper-radar.svg"


# --- Browser --------------------------------------------------------------------------------

def find_app_browser() -> str | None:
    """A Chromium-family browser that supports app windows, or None."""
    if override := os.environ.get("PAPER_RADAR_BROWSER"):
        return shutil.which(override) or (override if Path(override).exists() else None)
    if sys.platform == "darwin":
        return next((b for b in MAC_BROWSERS if Path(b).exists()), None)
    if sys.platform == "win32":
        return next((p for b in WINDOWS_BROWSERS if Path(p := os.path.expandvars(b)).exists()), None)
    return next((path for name in LINUX_BROWSERS if (path := shutil.which(name))), None)


def window_command(browser: str, url: str, profile: Path) -> list[str]:
    return [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        f"--class={WINDOW_CLASS}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1320,920",
    ]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_window_close(profile: Path, poll: float = 1.0) -> None:
    """Chrome hands new windows to a running process that uses the same profile, so the launched
    process can exit while the window stays open. The profile's SingletonLock names the live
    process ("host-pid"); wait until it goes away."""
    lock = profile / "SingletonLock"
    while os.path.lexists(lock):
        try:
            pid = int(os.readlink(lock).rsplit("-", 1)[1])
        except (OSError, ValueError, IndexError):
            return
        if not _pid_alive(pid):
            return
        time.sleep(poll)


# --- Server ---------------------------------------------------------------------------------

def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def choose_port(preferred: int) -> int:
    if port_is_free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def server_healthy(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=timeout) as response:
            return response.read().strip() == b"ok"
    except OSError:
        return False


def running_server() -> int | None:
    """The port of a Paper Radar server this user already has running, if any."""
    try:
        state = json.loads(state_file().read_text())
        pid, port = int(state["pid"]), int(state["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return port if _pid_alive(pid) and server_healthy(port) else None


def server_command(port: int) -> list[str]:
    return [
        sys.executable, "-m", "streamlit", "run", str(APP_SCRIPT),
        "--server.headless=true",
        f"--server.port={port}",
        "--server.address=127.0.0.1",
        "--browser.gatherUsageStats=false",
        "--server.fileWatcherType=none",
    ]


def server_env() -> dict[str, str]:
    env = os.environ.copy()
    # ROS and similar setups put another Python's packages on PYTHONPATH; keep them out.
    env.pop("PYTHONPATH", None)
    return env


def start_server(port: int, *, background: bool) -> subprocess.Popen:
    """Run Streamlit from the app folder so it picks up the bundled theme (.streamlit/config.toml)."""
    output = None
    if background:
        log = log_file()
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and log.stat().st_size > 5_000_000:
            log.unlink()
        output = open(log, "ab")  # noqa: SIM115 - handed to the child process
    return subprocess.Popen(
        server_command(port),
        cwd=APP_DIR,
        env=server_env(),
        stdout=output,
        stderr=subprocess.STDOUT if output else None,
        start_new_session=background and os.name != "nt",
    )


def wait_until_ready(process: subprocess.Popen, port: int, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"The app server stopped while starting. Details are in {log_file()}")
        if server_healthy(port):
            return
        time.sleep(0.3)
    raise RuntimeError(f"The app server didn't start within {timeout:.0f} seconds. Details are in {log_file()}")


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    try:
        state = json.loads(state_file().read_text())
        if int(state.get("pid", -1)) == process.pid:
            state_file().unlink()
    except (OSError, ValueError):
        pass


# --- Commands -------------------------------------------------------------------------------

def cmd_open(args: argparse.Namespace) -> int:
    port = running_server()
    owner: subprocess.Popen | None = None
    if port is None:
        port = choose_port(args.port)
        print(f"Starting Paper Radar on port {port}…")
        owner = start_server(port, background=True)
        state_file().parent.mkdir(parents=True, exist_ok=True)
        state_file().write_text(json.dumps({"pid": owner.pid, "port": port}))
        try:
            wait_until_ready(owner, port)
        except RuntimeError as exc:
            stop_server(owner)
            print(exc, file=sys.stderr)
            return 1
    url = f"http://127.0.0.1:{port}/"
    browser = None if args.browser else find_app_browser()
    try:
        if browser:
            profile = browser_profile()
            window = subprocess.Popen(
                window_command(browser, url, profile), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if owner is None:
                return 0  # another launcher owns the server and will stop it
            window.wait()
            wait_for_window_close(profile)
        else:
            webbrowser.open(url)
            if owner is None:
                return 0
            print(f"Paper Radar is open at {url}\nPress Ctrl+C here to stop it.")
            owner.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if owner is not None:
            stop_server(owner)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    port = args.port if port_is_free(args.port) else choose_port(args.port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Paper Radar is running at {url}  (Ctrl+C to stop)")
    process = start_server(port, background=False)
    try:
        if args.open:
            wait_until_ready(process, port)
            webbrowser.open(url)
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        stop_server(process)


def desktop_entry(executable: str) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        "Name=Paper Radar",
        "GenericName=Robotics paper reader",
        "Comment=Find, rank and read recent robotics papers",
        f'Exec="{executable}" open',
        "Icon=paper-radar",
        "Terminal=false",
        "Categories=Science;Literature;",
        "Keywords=arxiv;papers;robotics;research;reading;",
        f"StartupWMClass={WINDOW_CLASS}",
        "StartupNotify=true",
        "",
    ])


def cmd_install_desktop(args: argparse.Namespace) -> int:
    if not sys.platform.startswith("linux"):
        print("Desktop entries are only set up automatically on Linux. Run `paper-radar` to open the app.")
        return 1
    executable = shutil.which("paper-radar") or str(Path(sys.argv[0]).resolve())
    entry = applications_dir() / "paper-radar.desktop"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(desktop_entry(executable))
    entry.chmod(0o755)
    icon_path().parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS_DIR / "mark.svg", icon_path())
    if tool := shutil.which("update-desktop-database"):
        subprocess.run([tool, str(entry.parent)], check=False, capture_output=True)
    print(f"Added Paper Radar to your app menu ({entry}).\nIt runs: {executable} open")
    return 0


def cmd_uninstall_desktop(args: argparse.Namespace) -> int:
    for path in (applications_dir() / "paper-radar.desktop", icon_path()):
        if path.exists():
            path.unlink()
            print(f"Removed {path}")
    return 0


def cmd_paths(args: argparse.Namespace) -> int:
    from paper_radar.config import AppConfig

    config = AppConfig.from_env(load_env_file=False)
    rows = [
        ("Library database", config.db_path),
        ("Data folder", user_data_dir()),
        ("Settings folder", user_config_dir()),
        ("API key file", env_file()),
        ("Taxonomy in use", config.taxonomy_path),
        ("Profile defaults", config.profile_path),
        ("Server log", log_file()),
        ("App window browser", find_app_browser() or "none found; the default browser is used"),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label:<{width}}  {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paper-radar", description="Find, rank and read recent robotics papers.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    open_cmd = sub.add_parser("open", help="open the app in its own window (default)")
    open_cmd.add_argument("--port", type=int, default=DEFAULT_PORT)
    open_cmd.add_argument("--browser", action="store_true", help="use the default browser instead of an app window")
    serve_cmd = sub.add_parser("serve", help="run the local web server in this terminal")
    serve_cmd.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_cmd.add_argument("--open", action="store_true", help="also open it in the default browser")
    sub.add_parser("install-desktop", help="add Paper Radar to the app menu (Linux)")
    sub.add_parser("uninstall-desktop", help="remove the app menu entry")
    sub.add_parser("paths", help="show where data, settings and logs live")
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["open", *(argv or [])])
    handlers = {
        "open": cmd_open,
        "serve": cmd_serve,
        "install-desktop": cmd_install_desktop,
        "uninstall-desktop": cmd_uninstall_desktop,
        "paths": cmd_paths,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
