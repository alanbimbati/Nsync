"""The Syncthing core bundled with Nsync, run as a child process in a home of its own.

Nsync is a standalone app, not an add-on to a Syncthing you installed: it carries the
core, starts it, restarts it if it dies and stops it on exit. Its identity, folders and
database live under NSYNC_HOME/syncthing, so an existing Syncthing on the machine is untouched.
"""

import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import Config, home

log = logging.getLogger(__name__)

SYSTEM_BINARY = "/usr/lib/nsync/syncthing"


def find_binary() -> str | None:
    candidates = [
        os.environ.get("NSYNC_SYNCTHING"),
        SYSTEM_BINARY,
        str(Path(sys.executable).with_name("syncthing")),  # next to a frozen build
        str(Path(__file__).resolve().parent.parent / ".build" / "syncthing"),  # a checkout
        shutil.which("syncthing"),
    ]
    return next((c for c in candidates if c and os.access(c, os.X_OK)), None)


def state_dir() -> Path:
    return home() / "syncthing"


def free_port(preferred: int, host: str = "127.0.0.1") -> int:
    """The preferred port if nothing listens there, otherwise the next free one."""
    for port in range(preferred, preferred + 50):
        with socket.socket() as s:
            if s.connect_ex((host, port)) != 0:  # refused: nobody is there
                return port
    raise RuntimeError(f"no free port near {preferred}")


def ensure_settings(cfg: Config, recheck: bool = False) -> None:
    """Pick the API key and the ports once; they are kept in the Nsync config.

    With recheck (when running), a port saved earlier is verified again: a second instance, or
    anything else, may have taken it since, and the core would then fail to start.
    """
    changed = False
    if recheck:
        for name in ("gui_port", "sync_port"):
            saved = getattr(cfg, name)
            if saved and (now := free_port(saved, "0.0.0.0" if name == "sync_port" else "127.0.0.1")) != saved:
                setattr(cfg, name, now)
                changed = True
    if not cfg.syncthing_api_key:
        cfg.syncthing_api_key = secrets.token_urlsafe(24)
        changed = True
    if not cfg.sync_port:
        cfg.sync_port = free_port(22000, "0.0.0.0")  # 22000 is Syncthing's own; a second instance moves over
        changed = True
    if not cfg.gui_port:
        # Not 8384/8385: the first is a normal Syncthing's, the second is Nsync's own page.
        cfg.gui_port = free_port(18385)
        changed = True
    cfg.syncthing_url = f"http://127.0.0.1:{cfg.gui_port}"
    if changed:
        cfg.save()


class Core:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.binary = find_binary()
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _command(self) -> list[str]:
        return [self.binary, "serve", f"--home={state_dir()}", "--no-browser", "--no-restart", "--no-upgrade",
                "--no-port-probing", f"--gui-address=127.0.0.1:{self.cfg.gui_port}"]

    def _env(self) -> dict:
        env = dict(os.environ)
        env.update(STGUIAPIKEY=self.cfg.syncthing_api_key or "", STNOUPGRADE="1", STNORESTART="1",
                   STMONITORINGENABLED="0")
        return env

    def start(self) -> None:
        if not self.binary:
            raise RuntimeError("the Syncthing core was not found (expected %s)" % SYSTEM_BINARY)
        state_dir().mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._supervise, daemon=True)
        self._thread.start()

    def _supervise(self) -> None:
        delay = 2
        with open(home() / "syncthing.log", "a") as logfile:
            while not self._stop.is_set():
                started = time.monotonic()
                self._proc = subprocess.Popen(self._command(), env=self._env(), stdout=logfile, stderr=logfile)
                log.info("Syncthing core started (pid %s)", self._proc.pid)
                self._proc.wait()
                if self._stop.is_set():
                    return
                # Back off if it dies right away, so a broken setup does not spin.
                delay = 2 if time.monotonic() - started > 60 else min(delay * 2, 60)
                log.warning("Syncthing core exited with %s, restarting in %ss", self._proc.returncode, delay)
                self._stop.wait(delay)

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()

    def device_id(self) -> str:
        """The core's device id without it running; makes the keys on first use."""
        state_dir().mkdir(parents=True, exist_ok=True)
        if not (state_dir() / "cert.pem").exists():
            subprocess.run([self.binary, "generate", f"--home={state_dir()}"], check=True, capture_output=True)
        out = subprocess.run([self.binary, "device-id", f"--home={state_dir()}"], check=True,
                             capture_output=True, text=True)
        return out.stdout.strip()
