"""Keeps DropTray current from GitHub Releases: check, download, verify, install when idle, restart.

Installed with Setup.exe (an Inno uninstaller sits next to the exe): the new DropTraySetup.exe runs silently.
Installed from the zip or install.ps1: the new zip replaces the app folder. Running from source: report only.
Every download is checked against the release's SHA256SUMS.txt before anything runs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from core import APP, APP_VERSION, REPO, log

API = os.environ.get("DROPTRAY_UPDATE_URL") or f"https://api.github.com/repos/{REPO}/releases/latest"
FIRST_CHECK = 45               # seconds after start
CHECK_EVERY = 6 * 3600
IDLE_RETRY = 60                # while a transfer runs, try installing again this often
WORK = Path(tempfile.gettempdir()) / "DropTray-update"


def version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def fetch(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP}/{APP_VERSION}", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


class Updater:
    def __init__(self, app, restart):
        self.app = app
        self.restart = restart                 # quits DropTray so the update can replace it
        self.state = "idle"                    # idle, checking, current, available, downloading, ready, installing, error
        self.message = ""
        self.latest: dict | None = None
        self.progress = 0.0
        self.file: Path | None = None
        self.checked_at = 0.0
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.manual = self.manual_install = False

    # ---- what the drawer shows
    @property
    def packaged(self) -> bool:
        return bool(getattr(sys, "frozen", False))

    @property
    def via_setup(self) -> bool:
        return (Path(sys.executable).parent / "unins000.exe").exists()

    def status(self) -> dict:
        texts = {
            "idle": "Not checked yet",
            "checking": "Checking for updates",
            "current": "Up to date",
            "available": f"Version {self.latest['version']} is available" if self.latest else "",
            "downloading": f"Downloading {self.latest['version'] if self.latest else ''}: {self.progress:.0%}",
            "ready": f"Version {self.latest['version'] if self.latest else ''} is ready to install",
            "installing": "Installing the update, DropTray will restart",
            "error": self.message or "The update check failed",
        }
        return {"state": self.state, "text": texts.get(self.state, ""), "current": APP_VERSION,
                "latest": self.latest["version"] if self.latest else "", "progress": round(self.progress, 3),
                "auto": self.app.store.auto_update, "packaged": self.packaged,
                "checked": time.strftime("%H:%M", time.localtime(self.checked_at)) if self.checked_at else ""}

    # ---- the background loop
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def check_now(self):
        """From Settings or the tray: check (and, with auto-update on, carry on) right away."""
        self.manual = True
        self.wake.set()

    def install_now(self):
        self.manual_install = True
        self.wake.set()

    def _loop(self):
        self.wake.wait(FIRST_CHECK)
        while True:
            self.wake.clear()
            try:
                self._step()
            except Exception as e:
                log.warning("update step failed: %s", e)
                self.state, self.message = "error", "The update check failed. It will try again later."
            wait = IDLE_RETRY if self.state == "ready" else CHECK_EVERY
            self.wake.wait(wait)

    def _step(self):
        manual, manual_install = self.manual, self.manual_install
        self.manual = self.manual_install = False
        if self.state not in ("ready", "downloading") or manual:
            self.check()
        if self.state == "available" and self.packaged and (self.app.store.auto_update or manual_install):
            self.download()
        if self.state == "ready" and (self.app.store.auto_update or manual_install):
            if self._busy():
                log.info("update ready; waiting for transfers to finish")
                return
            self.install()

    def _busy(self) -> bool:
        a = self.app
        return bool(a.active or a.batches or a.gate.pending)

    # ---- the steps
    def check(self):
        self.state = "checking"
        with fetch(API) as r:
            rel = json.load(r)
        self.checked_at = time.time()
        version = rel.get("tag_name", "").lstrip("vV")
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
        if version and version_tuple(version) > version_tuple(APP_VERSION):
            if not self.latest or self.latest["version"] != version:
                self.file = None
                log.info("update available: %s -> %s", APP_VERSION, version)
            self.latest = {"version": version, "assets": assets, "notes": rel.get("body", "")[:2000],
                           "url": rel.get("html_url", "")}
            if self.state != "ready":
                self.state = "available"
        else:
            self.latest, self.state = None, "current"

    def download(self):
        name = "DropTraySetup.exe" if self.via_setup else "DropTray-win-x64.zip"
        assets = self.latest["assets"]
        if name not in assets or "SHA256SUMS.txt" not in assets:
            self.state, self.message = "error", f"The release has no {name}"
            return
        with fetch(assets["SHA256SUMS.txt"]) as r:
            sums = r.read().decode("utf-8", "replace")
        expected = next((ln.split()[0].lower() for ln in sums.splitlines() if ln.strip().endswith(name)), "")
        if not expected:
            self.state, self.message = "error", "The release's checksums don't list the download"
            return
        WORK.mkdir(parents=True, exist_ok=True)
        target = WORK / f"{self.latest['version']}-{name}"
        self.state, self.progress = "downloading", 0.0
        h = hashlib.sha256()
        with fetch(assets[name], timeout=60) as r, open(target, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(256 * 1024):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                self.progress = done / total if total else 0.0
        if h.hexdigest().lower() != expected:
            target.unlink(missing_ok=True)
            self.state, self.message = "error", "The download didn't match its checksum, so it was discarded"
            log.warning("update %s failed its checksum", name)
            return
        self.file, self.state, self.progress = target, "ready", 1.0
        log.info("update %s downloaded and verified", self.latest["version"])

    def install(self):
        if not self.file or not self.file.exists():
            self.state = "available"
            return
        self.state = "installing"
        version = self.latest["version"]
        exe = Path(sys.executable)
        q = lambda p: str(p).replace("'", "''")
        if self.file.suffix == ".exe":
            body = (f"$p = Start-Process -FilePath '{q(self.file)}' -PassThru "
                    "-ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TASKS=\"\"'\n"
                    "$p.WaitForExit()\n"
                    "Write-Host \"setup exited with $($p.ExitCode)\"")
        else:
            unpack = WORK / f"{version}-unpacked"
            body = (f"Remove-Item '{q(unpack)}' -Recurse -Force -ErrorAction SilentlyContinue\n"
                    f"Expand-Archive '{q(self.file)}' -DestinationPath '{q(unpack)}' -Force\n"
                    f"robocopy '{q(unpack)}' '{q(exe.parent)}' /MIR /XF unins000.exe unins000.dat /NFL /NDL /NJH /NJS /NP | Out-Null")
        script = WORK / "apply-update.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Continue'\n"
            f"Start-Transcript -Path '{q(WORK / 'apply.log')}' -Force | Out-Null\n"
            f"Wait-Process -Id {os.getpid()} -Timeout 30 -ErrorAction SilentlyContinue\n"
            "Get-Process DropTray -ErrorAction SilentlyContinue | Stop-Process -Force\n"
            f"{body}\n"
            f"Start-Process '{q(exe)}'\n"
            "Write-Host 'restarted'\n"
            "Stop-Transcript | Out-Null\n", encoding="utf-8")
        log.info("installing update %s", version)
        self.app.notifier.info(f"Updating {APP} to {version}", ["It will be back in a moment."])
        # A hidden console (not DETACHED_PROCESS: PowerShell stalled without one), its own process group,
        # and out of any job DropTray runs in, so it outlives DropTray and can replace it.
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | 0x01000000   # BREAKAWAY_FROM_JOB
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script)]
        try:
            subprocess.Popen(cmd, creationflags=flags, close_fds=True)
        except OSError:                                   # the job forbids breakaway: start it inside instead
            subprocess.Popen(cmd, creationflags=flags & ~0x01000000, close_fds=True)
        time.sleep(1.0)
        self.restart()


def note_updated(app):
    """On start: if this is a newer version than last time, say so once."""
    st = app.store
    last = st.last_version
    if last and version_tuple(APP_VERSION) > version_tuple(last):
        st.add_event("updated", f"Updated to {APP_VERSION}", f"from {last}")
        app.notifier.info(f"{APP} updated to {APP_VERSION}", [f"You were on {last}."])
    if last != APP_VERSION:
        st.last_version = APP_VERSION
        st.save_conf()
