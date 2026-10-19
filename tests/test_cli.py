import os
import socket
import subprocess
import sys
import threading
import time

from paper_radar import cli
from paper_radar.config import APP_DIR, APP_SCRIPT


def test_browser_override_and_window_flags(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "my-browser"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PAPER_RADAR_BROWSER", str(fake))
    assert cli.find_app_browser() == str(fake)
    command = cli.window_command(str(fake), "http://127.0.0.1:8765/", tmp_path / "profile")
    assert "--app=http://127.0.0.1:8765/" in command
    assert f"--user-data-dir={tmp_path / 'profile'}" in command
    assert "--class=paper-radar" in command and "--no-first-run" in command


def test_server_runs_the_packaged_script_without_foreign_pythonpath(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/opt/ros/humble/lib/python3.10/site-packages")
    command = cli.server_command(9999)
    assert command[:4] == [sys.executable, "-m", "streamlit", "run"]
    assert command[4] == str(APP_SCRIPT) and APP_SCRIPT.parent == APP_DIR
    assert "--server.address=127.0.0.1" in command and "--server.port=9999" in command
    assert (APP_DIR / ".streamlit" / "config.toml").is_file()
    assert "PYTHONPATH" not in cli.server_env()


def test_choose_port_falls_back_when_busy() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        assert not cli.port_is_free(taken)
        assert cli.choose_port(taken) != taken


def test_desktop_entry_and_install(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    entry = cli.desktop_entry("/home/me/.local/bin/paper-radar")
    assert 'Exec="/home/me/.local/bin/paper-radar" open' in entry
    assert "StartupWMClass=paper-radar" in entry and "Icon=paper-radar" in entry
    if sys.platform.startswith("linux"):
        assert cli.main(["install-desktop"]) == 0
        assert (tmp_path / "applications" / "paper-radar.desktop").is_file()
        assert cli.icon_path().is_file()
        assert cli.main(["uninstall-desktop"]) == 0
        assert not (tmp_path / "applications" / "paper-radar.desktop").exists()


def test_waits_while_the_profile_lock_names_a_live_process(tmp_path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.2)"])
    threading.Thread(target=sleeper.wait, daemon=True).start()  # reap it, as the OS would for a real browser
    os.symlink(f"host-{sleeper.pid}", profile / "SingletonLock")
    started = time.monotonic()
    cli.wait_for_window_close(profile, poll=0.1)  # returns once the "browser" process ends
    assert 0.5 <= time.monotonic() - started < 10


def test_no_running_server_without_state() -> None:
    assert cli.running_server() is None


def test_paths_lists_locations(capsys) -> None:
    assert cli.main(["paths"]) == 0
    output = capsys.readouterr().out
    assert "Library database" in output and "API key file" in output
