"""DropTray's receiver: storage, transfers, the accept gate, notifications and the HTTP server.

The UI (ui.py) only reads App's state and calls its methods; everything here is thread-safe
enough for one UI thread plus the server's request threads.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import mimetypes
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import uuid
import winreg
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image, ImageDraw, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:          # HEIC thumbnails and conversion just won't happen
    pillow_heif = None

APP = "DropTray"
APP_VERSION = "1.0.1"          # the one place the version lives; release.ps1 and the installer read it
REPO = "TechnoQuantum/droptray"
AUMID = "DropTray.Receiver"
HERE = Path(__file__).resolve().parent
CONF_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP
THUMBS = CONF_DIR / "thumbs"
DECISION_TIMEOUT = 90          # seconds a sender waits for Accept/Decline
TRUST_SECONDS = 300            # "Allow 5 min"
TICKET_SECONDS = 1800          # how long an accepted batch may keep uploading
CHUNK = 256 * 1024
MAX_SPEEDTEST = 256 * 1024 * 1024
MAX_TEXT = 100_000
HISTORY_KEEP = 500
MDNS_HOST = "droptray.local"
API_VERSION = 1                # bump when an endpoint changes incompatibly; see API.md
FEATURES = ["pair", "offer", "upload", "cancel", "text", "shared", "clipboard", "speedtest"]

CONF_DIR.mkdir(parents=True, exist_ok=True)
THUMBS.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=CONF_DIR / "droptray.log", level=logging.INFO, encoding="utf-8",
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(APP)


# ---------------------------------------------------------------- helpers

KINDS = {
    "image": {"jpg", "jpeg", "png", "heic", "heif", "gif", "webp", "bmp", "tif", "tiff", "dng", "raw", "avif"},
    "video": {"mov", "mp4", "m4v", "avi", "mkv", "webm", "hevc", "3gp"},
    "audio": {"m4a", "mp3", "wav", "aac", "flac", "ogg", "caf", "aiff"},
    "doc": {"pdf", "doc", "docx", "txt", "rtf", "pages", "key", "numbers", "xls", "xlsx", "ppt", "pptx",
            "csv", "md", "json", "epub"},
    "archive": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz"},
}
THUMBABLE = {"jpg", "jpeg", "png", "heic", "heif", "gif", "webp", "bmp", "tif", "tiff", "avif"}


def kind_of(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return next((k for k, exts in KINDS.items() if ext in exts), "other")


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_speed(bps: float) -> str:
    return fmt_bytes(bps) + "/s" if bps > 0 else "–"


def fmt_eta(seconds: float) -> str:
    if seconds <= 0 or seconds > 359999:
        return ""
    if seconds < 60:
        return f"{seconds:.0f} s left"
    return f"{seconds // 60:.0f} min {seconds % 60:02.0f} s left"


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the default route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def link_info(ip: str) -> str:
    """Adapter name and link speed for the interface holding `ip`, e.g. 'Ethernet 2 · 2.5 Gbps'."""
    cmd = (f"$a = Get-NetIPAddress -IPAddress '{ip}' -ErrorAction Stop | Get-NetAdapter;"
           " \"$($a.Name)|$($a.LinkSpeed)\"")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                             text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        name, _, speed = out.stdout.strip().partition("|")
        return f"{name} · {speed}" if speed else "unknown"
    except Exception:
        return "unknown"


def safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if Path(name).stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                                   *(f"LPT{i}" for i in range(10))}:
        name = "_" + name
    return name[:180] or "file"


def unique_path(folder: Path, name: str) -> Path:
    path = folder / name
    stem, ext, i = path.stem, path.suffix, 1
    while path.exists():
        path = folder / f"{stem} ({i}){ext}"
        i += 1
    return path


def make_thumb(path: Path, size: int = 128, cached_only: bool = False) -> str:
    """Square thumbnail cached under %APPDATA%; '' when the file isn't a picture we can read.

    cached_only: return '' instead of decoding the image now (for UI paths that must stay fast)."""
    if path.suffix.lower().lstrip(".") not in THUMBABLE:
        return ""
    try:
        st = path.stat()
        key = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{size}".encode()).hexdigest()[:20]
        out = THUMBS / f"{key}.png"
        if not out.exists() and cached_only:
            return ""
        if not out.exists():
            with Image.open(path) as im:
                im.draft("RGB", (size * 2, size * 2))
                im = ImageOps.exif_transpose(im).convert("RGB")
                ImageOps.fit(im, (size, size), Image.LANCZOS).save(out)
        return str(out)
    except Exception as e:
        log.info("no thumbnail for %s: %s", path.name, e)
        return ""


def heic_to_jpg(path: Path) -> Path:
    if pillow_heif is None or path.suffix.lower() not in (".heic", ".heif"):
        return path
    try:
        with Image.open(path) as im:
            exif = im.info.get("exif")
            target = unique_path(path.parent, path.stem + ".jpg")
            kw = {"quality": 92}
            if exif:
                kw["exif"] = exif
            im.convert("RGB").save(target, "JPEG", **kw)
        os.utime(target, (path.stat().st_atime, path.stat().st_mtime))
        path.unlink()
        return target
    except Exception as e:
        log.warning("HEIC conversion failed for %s: %s", path.name, e)
        return path


def gradient(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def make_icon(size: int = 64, state: str = "idle") -> Image.Image:
    """Rounded tile with a down arrow into a tray; the tile's colour shows the state."""
    colors = {"idle": ("#6366f1", "#22d3ee"), "ask": ("#f59e0b", "#f97316"),
              "busy": ("#10b981", "#22d3ee"), "paused": ("#6b7280", "#9ca3af")}[state]
    big = size * 4
    grad = Image.new("RGBA", (big, big))
    gd = ImageDraw.Draw(grad)
    for y in range(big):
        gd.line([(0, y), (big, y)], fill=gradient(*colors, y / big))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((4, 4, big - 4, big - 4), radius=big * 0.24, fill=255)
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    s = big / 64
    white = (255, 255, 255, 255)
    d.rounded_rectangle((28 * s, 12 * s, 36 * s, 34 * s), radius=2 * s, fill=white)
    d.polygon([(18 * s, 29 * s), (46 * s, 29 * s), (32 * s, 44 * s)], fill=white)
    d.line([(14 * s, 41 * s), (14 * s, 51 * s), (50 * s, 51 * s), (50 * s, 41 * s)], fill=white,
           width=int(5 * s), joint="curve")
    return img.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- config & history

class Store:
    SETTINGS = ("port", "token", "save_dir", "auto_accept", "auto_copy", "convert_heic", "clip_sync",
                "pc_id", "pc_name", "devices", "blocks", "keep_open", "widget", "widget_pos", "drawer_at", "glass",
                "auto_update", "last_version")
    # When each Activity block shows: always, busy (while receiving), idle, or off.
    BLOCK_DEFAULTS = {"throughput": "busy", "totals": "always", "recent": "always", "connection": "off"}

    def __init__(self):
        self.conf_file = CONF_DIR / "config.json"
        self.hist_file = CONF_DIR / "history.json"
        self.port = 8765
        self.token = secrets.token_hex(3).upper()     # six characters: easy to type on a phone
        self.save_dir = str(Path.home() / "Downloads" / APP)
        self.auto_accept = False
        self.auto_copy = True
        self.convert_heic = False
        self.clip_sync = False               # push every new PC clipboard text to phones
        self.pc_id = uuid.uuid4().hex[:12]   # lets a phone tell this PC from the one at work
        self.pc_name = socket.gethostname()
        self.devices: list[dict] = []        # paired phones: id, name, key, paired, last_seen, ip
        self.blocks = dict(self.BLOCK_DEFAULTS)
        self.keep_open = False               # popover stays open when clicking elsewhere
        self.widget = False                  # the desktop tile
        self.widget_pos: list | None = None  # its top-left, logical px
        self.glass = "frosted"               # window material: frosted (acrylic), clear, or mica
        self.auto_update = True              # download and install new releases by itself
        self.last_version = ""               # the version that ran last, to announce an update once
        self.drawer_at = "tray"              # where the drawer opens: "tray" (corner) or "widget" (beside it)
        self.totals = {"files": 0, "bytes": 0, "seconds": 0.0, "peak": 0.0}
        self.records: list[dict] = []
        self.texts: list[dict] = []          # text and links received from phones
        self.events: list[dict] = []         # the activity log: what happened, newest last
        self.shared: list[dict] = []         # what the PC offers to phones
        self.speedtest: dict | None = None
        self.version = 0                     # bumped on every change the UI should redraw
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        try:
            data = json.loads(self.conf_file.read_text("utf-8"))
            for k in self.SETTINGS:
                if k in data:
                    setattr(self, k, data[k])
            self.blocks = {**self.BLOCK_DEFAULTS, **(self.blocks if isinstance(self.blocks, dict) else {})}
        except (OSError, ValueError):
            pass
        try:
            data = json.loads(self.hist_file.read_text("utf-8"))
            self.totals.update(data.get("totals", {}))
            self.records = data.get("records", [])[-HISTORY_KEEP:]
            self.texts = data.get("texts", [])[-50:]
            self.events = data.get("events", [])[-100:]
            self.shared = [s for s in data.get("shared", []) if s["kind"] == "text" or Path(s["path"]).exists()]
            self.speedtest = data.get("speedtest")
        except (OSError, ValueError):
            pass
        self.save_conf()

    def save_conf(self):
        with self.write_lock:
            with self.lock:
                text = json.dumps({k: getattr(self, k) for k in self.SETTINGS}, indent=2)
                self.version += 1
            self._write(self.conf_file, text)

    def save_history(self):
        # Two uploads can finish at once. The write lock serialises them, and the snapshot is taken
        # inside it so a newer state is never overwritten by an older one.
        with self.write_lock:
            with self.lock:
                data = {"totals": self.totals, "records": self.records, "texts": self.texts,
                        "shared": self.shared, "speedtest": self.speedtest, "events": self.events}
                self.version += 1
                text = json.dumps(data, indent=1)
            self._write(self.hist_file, text)

    @staticmethod
    def _write(path: Path, text: str):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, "utf-8")
        for _ in range(20):            # antivirus or the indexer may hold the file for a moment
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.05)
        log.warning("could not save %s: the file stayed busy", path.name)

    def add_record(self, rec: dict):
        with self.lock:
            self.records.append(rec)
            del self.records[:-HISTORY_KEEP]
            if rec["status"] == "received":
                t = self.totals
                t["files"] += 1
                t["bytes"] += rec["size"]
                t["seconds"] += rec["seconds"]
                t["peak"] = max(t["peak"], rec["peak"])
        self.save_history()

    def add_event(self, kind: str, title: str, sub: str = "", thumbs: list[str] | None = None,
                  path: str = "", link: str = "", save: bool = True) -> dict:
        ev = {"id": uuid.uuid4().hex[:10], "kind": kind, "title": title, "sub": sub, "when": time.time(),
              "thumbs": (thumbs or [])[:4], "path": path, "link": link}
        with self.lock:
            self.events.append(ev)
            del self.events[:-100]
        if save:
            self.save_history()
        return ev

    def today(self) -> tuple[int, int]:
        start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        with self.lock:
            recs = [r for r in self.records if r["status"] == "received" and r["when"] >= start]
        return len(recs), sum(r["size"] for r in recs)

    def add_text(self, text: str, device: str):
        with self.lock:
            self.texts.append({"id": uuid.uuid4().hex[:12], "text": text, "device": device, "when": time.time()})
            del self.texts[:-50]
        self.save_history()

    def share(self, item: dict):
        self.share_many([item])

    def share_many(self, items: list[dict]):
        if not items:
            return
        with self.lock:
            self.shared.extend(items)
            del self.shared[:-100]
        self.save_history()

    def unshare(self, item_id: str):
        with self.lock:
            self.shared = [s for s in self.shared if s["id"] != item_id]
        self.save_history()

    def device_by_key(self, key: str) -> dict | None:
        with self.lock:
            return next((d for d in self.devices if secrets.compare_digest(d["key"], key)), None)

    def add_device(self, name: str, ip: str) -> dict:
        d = {"id": uuid.uuid4().hex[:12], "name": name[:40] or "Phone", "key": secrets.token_urlsafe(24),
             "paired": time.time(), "last_seen": time.time(), "ip": ip}
        with self.lock:
            self.devices.append(d)
        self.save_conf()
        return d

    def remove_device(self, dev_id: str):
        with self.lock:
            self.devices = [d for d in self.devices if d["id"] != dev_id]
        self.save_conf()

    def shared_item(self, item_id: str) -> dict | None:
        with self.lock:
            return next((s for s in self.shared if s["id"] == item_id), None)


# ---------------------------------------------------------------- live transfers

class Meter:
    """Aggregate throughput across all transfers, sampled by the UI's clock."""

    def __init__(self):
        self.total = 0
        self.samples: deque[float] = deque(maxlen=120)
        self.speed = 0.0
        self._t, self._b = time.perf_counter(), 0

    def add(self, n: int):
        self.total += n

    def sample(self, active: bool):
        now = time.perf_counter()
        dt = now - self._t
        if dt < 0.2:
            return
        inst = (self.total - self._b) / dt
        self._t, self._b = now, self.total
        if active:
            self.speed = inst if not self.samples else 0.55 * inst + 0.45 * self.speed
            self.samples.append(self.speed)
        else:
            self.speed = 0.0


class Batch:
    def __init__(self, files: int, nbytes: int, sender: str):
        self.id = uuid.uuid4().hex
        self.files, self.bytes, self.sender = files, nbytes, sender
        self.done_files = self.failed = 0
        self.done_bytes = 0
        self.saved: list[Path] = []
        self.cancelled = False
        self.start = time.perf_counter()
        self.touched = time.perf_counter()
        self.peak = 0.0

    @property
    def finished(self) -> bool:
        return self.cancelled or self.done_files + self.failed >= self.files


class Transfer:
    def __init__(self, name: str, total: int, sender: str, batch: Batch, meter: Meter):
        self.id = uuid.uuid4().hex
        self.name, self.total, self.sender, self.batch = name, total, sender, batch
        self.meter = meter
        self.done = 0
        self.start = time.perf_counter()
        self.peak = 0.0
        self.cancelled = False
        self._mark_t, self._mark_b = self.start, 0

    def add(self, n: int):
        self.done += n
        self.meter.add(n)
        now = time.perf_counter()
        dt = now - self._mark_t
        if dt >= 0.25:
            self.peak = max(self.peak, (self.done - self._mark_b) / dt)
            self._mark_t, self._mark_b = now, self.done

    @property
    def elapsed(self) -> float:
        return max(time.perf_counter() - self.start, 1e-6)


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- notifications

def register_aumid(icon_png: Path):
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{AUMID}")
    winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP)
    winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon_png))
    winreg.CloseKey(key)


class Notifier:
    def __init__(self, icon_png: Path):
        from windows_toasts import InteractableWindowsToaster
        try:
            register_aumid(icon_png)
            self.toaster = InteractableWindowsToaster(APP, AUMID)
        except Exception:
            self.toaster = InteractableWindowsToaster(APP)

    def ask(self, title: str, lines: list[str], on_choice, image: str = "", trust_button: bool = True):
        """Accept/Decline toast. on_choice gets 'accept', 'trust', 'decline', 'open' or None (timed out)."""
        from windows_toasts import (Toast, ToastButton, ToastButtonColour, ToastDisplayImage,
                                    ToastImagePosition, ToastScenario)
        toast = Toast([title, *lines], scenario=ToastScenario.Reminder)
        if image:
            toast.AddImage(ToastDisplayImage.fromPath(image, position=ToastImagePosition.AppLogo))
        toast.AddAction(ToastButton("Accept", "accept", colour=ToastButtonColour.Green))
        if trust_button:
            toast.AddAction(ToastButton("Allow 5 min", "trust"))
        toast.AddAction(ToastButton("Decline", "decline", colour=ToastButtonColour.Red))

        def activated(e):
            log.info("toast activated: %r", e.arguments)
            on_choice(e.arguments if e.arguments in ("accept", "trust", "decline") else "open")

        def dismissed(e):
            log.info("toast dismissed: %r", getattr(e, "reason", None))
            on_choice(None)
        toast.on_activated = activated
        toast.on_dismissed = dismissed
        return toast if self._show(toast) else None

    def info(self, title: str, lines: list[str], buttons: dict | None = None, image: str = "",
             on_click=None):
        """Plain toast; `buttons` maps a label to a no-argument callback."""
        from windows_toasts import Toast, ToastButton, ToastDisplayImage, ToastImagePosition
        toast = Toast([title, *lines])
        if image:
            toast.AddImage(ToastDisplayImage.fromPath(image, position=ToastImagePosition.Hero))
        actions = dict(buttons or {})
        for i, label in enumerate(actions):
            toast.AddAction(ToastButton(label, f"b{i}"))

        def activated(e):
            try:
                if e.arguments and e.arguments.startswith("b"):
                    list(actions.values())[int(e.arguments[1:])]()
                elif on_click:
                    on_click()
            except Exception as ex:
                log.warning("toast action failed: %s", ex)
        toast.on_activated = activated
        self._show(toast)

    def _show(self, toast) -> bool:
        try:
            self.toaster.show_toast(toast)
            return True
        except Exception as e:
            log.warning("toast failed: %s", e)
            return False

    def remove(self, toast):
        try:
            self.toaster.remove_toast(toast)
        except Exception:
            pass


# ---------------------------------------------------------------- accept gate

class Prompt:
    def __init__(self, title: str, lines: list[str], sender: str, files: list[str], pairing: bool = False):
        self.pairing = pairing
        self.id = uuid.uuid4().hex
        self.title, self.lines, self.sender, self.files = title, lines, sender, files
        self.event = threading.Event()
        self.choice: str | None = None
        self.toast = None
        self.created = time.perf_counter()

    def decide(self, choice: str | None):
        log.info("prompt %s: choice %r", self.id[:8], choice)
        if self.event.is_set() or choice in (None, "open"):
            return
        self.choice = choice
        self.event.set()


class Gate:
    def __init__(self, app: "App"):
        self.app = app
        self.trust_until = 0.0
        self.paused = False
        self.pending: dict[str, Prompt] = {}
        self.tickets: dict[str, dict] = {}
        self.lock = threading.Lock()

    def ask(self, title: str, lines: list[str], sender: str, files: list[str],
            pairing: bool = False) -> tuple[bool, str]:
        if self.paused:
            return False, "Receiving is paused on the PC"
        if not pairing and (self.app.store.auto_accept or time.time() < self.trust_until):
            return True, ""
        p = Prompt(title, lines, sender, files, pairing)
        with self.lock:
            self.pending[p.id] = p
        self.app.ui(("prompt", p.id))

        def on_toast(choice):
            if choice is None and not p.event.is_set():
                self.app.ui(("show", None))     # timed out off screen: bring the drawer up
            elif choice == "open":
                self.app.ui(("show", None))
            p.decide(choice)

        # The open drawer already shows the request; a toast on top of it would only cover it.
        p.toast = None if self.app.ui_visible else self.app.notifier.ask(title, lines, on_toast,
                                                                          trust_button=not pairing)
        if p.toast is None and not self.app.ui_visible:
            self.app.ui(("show", None))         # no notifications: pop the drawer instead
        p.event.wait(DECISION_TIMEOUT)
        with self.lock:
            self.pending.pop(p.id, None)
        if p.toast is not None:
            self.app.notifier.remove(p.toast)
        self.app.ui(("prompt", None))
        log.info("prompt %s: %s -> %s", p.id[:8], title, p.choice)
        if p.choice == "trust" and not pairing:
            self.trust_until = time.time() + TRUST_SECONDS
        if p.choice in ("accept", "trust"):
            return True, ""
        return False, "Declined on the PC" if p.choice == "decline" else "No answer on the PC"

    def issue_ticket(self, batch: Batch) -> str:
        t = secrets.token_urlsafe(12)
        with self.lock:
            now = time.time()
            self.tickets = {k: v for k, v in self.tickets.items() if v["expires"] > now}
            self.tickets[t] = {"left": batch.files, "expires": now + TICKET_SECONDS, "batch": batch}
        return t

    def use_ticket(self, t: str) -> Batch | None:
        with self.lock:
            v = self.tickets.get(t)
            if not v or v["expires"] < time.time() or v["left"] <= 0 or v["batch"].cancelled:
                return None
            v["left"] -= 1
            return v["batch"]


# ---------------------------------------------------------------- HTTP

def multipart_events(chunks, boundary: bytes):
    """Stream a multipart/form-data body: yields ('start', headers), ('data', bytes), ('end', None)."""
    delim = b"\r\n--" + boundary
    buf = b"\r\n"
    it = iter(chunks)
    state = "preamble"

    def more() -> bool:
        nonlocal buf
        c = next(it, None)
        if c is None:
            return False
        buf += c
        return True

    while True:
        if state == "preamble":
            i = buf.find(delim)
            if i < 0:
                buf = buf[-len(delim):]
                if not more():
                    return
                continue
            buf = buf[i + len(delim):]
            state = "headers"
        elif state == "headers":
            if len(buf) < 2 and not more():
                return
            if buf[:2] == b"--":
                return
            j = buf.find(b"\r\n\r\n")
            if j < 0:
                if not more():
                    return
                continue
            headers = buf[2:j].decode("utf-8", "replace")
            buf = buf[j + 4:]
            state = "body"
            yield "start", headers
        else:
            i = buf.find(delim)
            if i >= 0:
                if i:
                    yield "data", buf[:i]
                buf = buf[i + len(delim):]
                state = "headers"
                yield "end", None
                continue
            keep = len(delim) - 1
            if len(buf) > keep:
                yield "data", buf[:-keep]
                buf = buf[-keep:]
            if not more():
                return


def filename_from(headers: str) -> str:
    m = re.search(r"filename\*=UTF-8''([^;\r\n]+)", headers, re.I)
    if m:
        return unquote(m.group(1))
    m = re.search(r'filename="([^"]*)"', headers, re.I) or re.search(r"filename=([^;\r\n]+)", headers, re.I)
    return m.group(1).strip() if m else ""


def describe_offer(dev: str, names: list[str], total: int) -> tuple[str, list[str]]:
    if len(names) == 1:
        return f"{dev} wants to send a file", [f"{names[0]} · {fmt_bytes(total)}" if total else names[0]]
    kinds = [kind_of(n) for n in names]
    parts = []
    for k, word in (("image", "photo"), ("video", "video")):
        c = kinds.count(k)
        if c:
            parts.append(f"{c} {word}{'s' if c > 1 else ''}")
    rest = len(names) - sum(kinds.count(k) for k in ("image", "video"))
    if rest:
        parts.append(f"{rest} file{'s' if rest > 1 else ''}")
    return f"{dev} wants to send {len(names)} items", [f"{', '.join(parts)} · {fmt_bytes(total)}"]


def make_handler(app: "App"):
    web = HERE / "web" / "index.html"
    icon_buf = io.BytesIO()
    make_icon(180).save(icon_buf, "PNG")
    icon_png = icon_buf.getvalue()
    noise = os.urandom(1024 * 1024)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = APP

        def log_message(self, *args):
            pass

        # ---- plumbing
        def send(self, code: int, body: bytes = b"", ctype: str = "application/json", close: bool = False,
                 headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            if close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(body)

        def json(self, code: int, obj, close: bool = False):
            self.send(code, json.dumps(obj).encode(), close=close)

        def authed(self, q):
            """The paired device sending this, 'code' for the pairing code (Shortcuts), or None."""
            given = (self.headers.get("X-Token") or (q.get("t") or [""])[0]).strip()
            if not given:
                return None
            if secrets.compare_digest(given.upper(), app.store.token.upper()):
                return "code"
            dev = app.store.device_by_key(given)
            if dev:
                dev["last_seen"], dev["ip"] = time.time(), self.client_address[0]
            return dev

        def body_chunks(self):
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                while True:
                    size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                    if size == 0:
                        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                            pass
                        return
                    left = size
                    while left:
                        c = self.rfile.read(min(CHUNK, left))
                        if not c:
                            return
                        left -= len(c)
                        yield c
                    self.rfile.readline()
            else:
                left = int(self.headers.get("Content-Length") or 0)
                while left > 0:
                    c = self.rfile.read(min(CHUNK, left))
                    if not c:
                        return
                    left -= len(c)
                    yield c

        def read_json(self, limit: int = 1024 * 1024) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def device(self) -> str:
            return (self.headers.get("X-Device") or "").strip()[:40] or "A device"

        def sender(self) -> str:
            return f"{self.device()} ({self.client_address[0]})"

        def send_file(self, path: Path, ctype: str, disposition: str):
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Disposition", disposition)
            self.end_headers()
            with open(path, "rb") as f:
                while c := f.read(CHUNK):
                    self.wfile.write(c)

        # ---- routes
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self.send(200, web.read_bytes(), "text/html; charset=utf-8")
            if u.path in ("/icon.png", "/apple-touch-icon.png"):
                return self.send(200, icon_png, "image/png")
            if u.path == "/local/show" and self.client_address[0] == "127.0.0.1":
                app.ui(("show", None))             # DropTray was started again: open the drawer instead
                return self.json(200, {"ok": True, "app": APP})
            m = re.fullmatch(r"/local/thumb/([0-9a-f]{20}\.png)", u.path)
            if m:                                  # thumbnails for the drawer: this PC only, this session only
                if self.client_address[0] != "127.0.0.1" or not secrets.compare_digest(
                        (q.get("k") or [""])[0], app.local_key):
                    return self.json(403, {"ok": False})
                f = THUMBS / m.group(1)
                if not f.exists():
                    return self.json(404, {"ok": False})
                return self.send(200, f.read_bytes(), "image/png", headers={"Cache-Control": "max-age=86400"})
            if u.path == "/hello":                 # which PC is this? the page picks its key by id
                body = {"app": APP, "version": APP_VERSION, "api": API_VERSION, "features": FEATURES, "id": app.store.pc_id,
                        "name": app.store.pc_name, "mdns": app.mdns_ok}
                return self.send(200, json.dumps(body).encode(), headers={"Access-Control-Allow-Origin": "*"})
            if not self.authed(q):
                return self.json(401, {"ok": False, "reason": "Wrong pairing code"})
            if u.path == "/ping":
                dev = self.authed(q)
                return self.json(200, {"ok": True, "name": app.store.pc_name, "id": app.store.pc_id,
                                       "paused": app.gate.paused, "shared": len(app.store.shared),
                                       "device": dev["name"] if isinstance(dev, dict) else ""})
            if u.path == "/clipboard":           # the PC clipboard, if the user lets phones see it
                pub = app.clip_public()
                if q.get("plain"):                 # the "Paste from PC" Shortcut
                    return self.send(200, (pub.get("text") or "").encode("utf-8"), "text/plain; charset=utf-8")
                return self.json(200, pub)
            m = re.fullmatch(r"/clipboard/(file|thumb)/(\d+)|/clipboard/image", u.path)
            if m:
                c = app.clip
                if not app.store.clip_sync:
                    return self.json(403, {"ok": False, "reason": "Clipboard sharing is off on the PC"})
                if u.path == "/clipboard/image":
                    if c["kind"] != "image" or not c["image"]:
                        return self.json(404, {"ok": False})
                    return self.send_file(Path(c["image"]), "image/png",
                                          "inline; filename*=UTF-8''" + quote("Copied image.png"))
                i = int(m.group(2))
                if c["kind"] != "files" or i >= len(c["files"]) or not Path(c["files"][i]).is_file():
                    return self.json(404, {"ok": False})
                f = Path(c["files"][i])
                if m.group(1) == "thumb":
                    t = make_thumb(f, 160)
                    return self.send(200, Path(t).read_bytes(), "image/png") if t else self.json(404, {"ok": False})
                how = "inline" if q.get("inline") else "attachment"
                return self.send_file(f, mimetypes.guess_type(f.name)[0] or "application/octet-stream",
                                      f"{how}; filename*=UTF-8''{quote(f.name)}")
            if u.path == "/shared":
                with app.store.lock:
                    items = [{k: s[k] for k in ("id", "kind", "name", "size", "when", "text") if k in s}
                             for s in reversed(app.store.shared)]
                for it in items:
                    it["thumb"] = it["kind"] == "file" and kind_of(it["name"]) == "image"
                return self.json(200, {"items": items})
            m = re.fullmatch(r"/shared/([0-9a-f]+)(/thumb)?", u.path)
            if m:
                item = app.store.shared_item(m.group(1))
                if not item or item["kind"] != "file" or not Path(item["path"]).exists():
                    return self.json(404, {"ok": False, "reason": "No longer shared"})
                path = Path(item["path"])
                if m.group(2):
                    thumb = make_thumb(path, 160)
                    if not thumb:
                        return self.json(404, {"ok": False})
                    return self.send(200, Path(thumb).read_bytes(), "image/png")
                ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                how = "inline" if q.get("inline") else "attachment"
                log.info("phone downloading %s", path.name)
                dev = self.authed(q)
                app.note_download(item, dev["name"] if isinstance(dev, dict) else "Your phone")
                return self.send_file(path, ctype, f"{how}; filename*=UTF-8''{quote(path.name)}")
            if u.path == "/speedtest/down":
                n = max(0, min(int((q.get("bytes") or ["0"])[0]), MAX_SPEEDTEST))
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(n))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while n > 0:
                    k = min(n, len(noise))
                    self.wfile.write(noise[:k])
                    n -= k
                return
            self.json(404, {"ok": False})

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/pair":
                try:
                    return self.pair()
                except (ValueError, TypeError) as e:
                    return self.json(400, {"ok": False, "reason": str(e)}, close=True)
            if not self.authed(q):
                return self.json(401, {"ok": False, "reason": "Wrong pairing code"}, close=True)
            try:
                if u.path == "/offer":
                    return self.offer()
                if u.path == "/upload":
                    return self.upload(q)
                if u.path == "/cancel":
                    r = self.read_json()
                    app.cancel_ticket(str(r.get("ticket", "")))
                    return self.json(200, {"ok": True})
                if u.path == "/text":
                    return self.text()
                if u.path == "/speedtest/up":
                    t0, n = time.perf_counter(), 0
                    for c in self.body_chunks():
                        n += len(c)
                    return self.json(200, {"bytes": n, "seconds": time.perf_counter() - t0})
                if u.path == "/speedtest/result":
                    r = self.read_json()
                    sp = {k: float(r.get(k, 0)) for k in ("up_mbps", "down_mbps", "ping_ms")}
                    sp.update(when=time.time(), device=str(r.get("device", ""))[:40])
                    app.store.speedtest = sp
                    app.store.save_history()
                    return self.json(200, {"ok": True})
            except (ValueError, KeyError, TypeError) as e:
                return self.json(400, {"ok": False, "reason": str(e)}, close=True)
            self.json(404, {"ok": False}, close=True)

        def pair(self):
            r = self.read_json()
            name = str(r.get("device") or "Phone").strip()[:40] or "Phone"
            code = str(r.get("code") or "").strip().upper()
            ip = self.client_address[0]
            if code:
                if not secrets.compare_digest(code, app.store.token.upper()):
                    time.sleep(1)                  # slow down guessing
                    return self.json(200, {"ok": False, "reason": "Wrong pairing code"})
            else:
                with app.lock:
                    if ip in app.pairing_ips:
                        return self.json(200, {"ok": False, "reason": "Already waiting for the PC"})
                    app.pairing_ips.add(ip)
                try:
                    ok, reason = app.gate.ask(f"{name} wants to pair",
                                              [f"from {ip} · it can then send files to this PC"],
                                              f"{name} ({ip})", [], pairing=True)
                finally:
                    app.pairing_ips.discard(ip)
                if not ok:
                    return self.json(200, {"ok": False, "reason": reason})
            dev = app.store.add_device(name, ip)
            log.info("paired %s (%s)", name, ip)
            app.store.add_event("paired", f"Paired with {name}", f"from {ip}")
            app.notifier.info(f"Paired with {name}", [f"{name} can now send to {app.store.pc_name}"])
            return self.json(200, {"ok": True, "key": dev["key"], "id": app.store.pc_id,
                                   "name": app.store.pc_name, "device_id": dev["id"]})

        def text(self):
            if self.headers.get("Content-Type", "").startswith("text/plain"):    # Shortcuts: raw clipboard
                n = int(self.headers.get("Content-Length") or 0)
                r = {"text": self.rfile.read(min(n, MAX_TEXT * 4)).decode("utf-8", "replace")}
            else:
                r = self.read_json(limit=MAX_TEXT * 4)
            text = str(r.get("text") or "")[:MAX_TEXT].strip()
            if not text:
                return self.json(400, {"ok": False, "reason": "Empty"})
            if app.gate.paused:
                return self.json(200, {"ok": False, "reason": "Receiving is paused on the PC"})
            app.receive_text(text, str(r.get("device") or self.device())[:40])
            return self.json(200, {"ok": True, "copied": app.store.auto_copy})

        def offer(self):
            r = self.read_json()
            files = r.get("files") or []
            if not isinstance(files, list) or not files:
                return self.json(400, {"ok": False, "reason": "No files"})
            dev = str(r.get("device") or "A device")[:40]
            names = [safe_name(str(f.get("name", "file"))) for f in files]
            total = sum(int(f.get("size") or 0) for f in files)
            title, lines = describe_offer(dev, names, total)
            sender = f"{dev} ({self.client_address[0]})"
            ok, reason = app.gate.ask(title, lines, sender, names)
            if not ok:
                if reason != "Receiving is paused on the PC":
                    app.store.add_event("declined", f"{reason.replace(' on the PC', '')}: {title}", lines[0])
                return self.json(200, {"ok": False, "reason": reason})
            batch = app.new_batch(len(files), total, sender)
            self.json(200, {"ok": True, "ticket": app.gate.issue_ticket(batch), "batch": batch.id})

        def upload(self, q):
            ctype = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length") or 0)
            chunks = self.body_chunks()
            m = re.search(r'boundary="?([^";]+)"?', ctype) if ctype.lower().startswith("multipart/") else None
            if m:
                events = multipart_events(chunks, m.group(1).encode())
                name = ""
                for kind, val in events:          # skip to the first part that is a file
                    if kind == "start" and filename_from(val):
                        name = filename_from(val)
                        break
                if not name:
                    return self.json(400, {"ok": False, "reason": "No file in the form"}, close=True)

                def data():
                    for kind, val in events:
                        if kind == "data":
                            yield val
                        elif kind == "end":
                            return
                stream = data()
            else:
                name = unquote(self.headers.get("X-Filename", "")) or filename_from(
                    self.headers.get("Content-Disposition", ""))
                if not name:
                    ext = mimetypes.guess_extension(ctype.split(";")[0].strip()) or ""
                    name = time.strftime("received-%Y%m%d-%H%M%S") + ext
                stream = chunks
            name = safe_name(name)

            ticket = self.headers.get("X-Ticket") or (q.get("ticket") or [""])[0]
            batch = app.gate.use_ticket(ticket) if ticket else None
            if batch is None:
                if ticket and app.ticket_cancelled(ticket):
                    return self.send(409, b"Cancelled on the PC", "text/plain; charset=utf-8", close=True)
                title, lines = describe_offer(self.device(), [name], length)
                ok, reason = app.gate.ask(title, lines, self.sender(), [name])
                if not ok:
                    app.store.add_record({"name": name, "size": length, "seconds": 0, "peak": 0,
                                          "sender": self.sender(), "when": time.time(), "kind": kind_of(name),
                                          "status": "declined", "path": "", "thumb": ""})
                    return self.send(403, reason.encode(), "text/plain; charset=utf-8", close=True)
                batch = app.new_batch(1, length, self.sender())

            folder = Path(app.store.save_dir)
            folder.mkdir(parents=True, exist_ok=True)
            tr = Transfer(name, length if not m else 0, self.sender(), batch, app.meter)
            app.begin(tr)
            part = folder / f".{tr.id}.part"
            status, final = "failed", None
            try:
                with open(part, "wb") as f:
                    for c in stream:
                        if tr.cancelled or batch.cancelled:
                            raise Cancelled()
                        f.write(c)
                        tr.add(len(c))
                if m is None and length and tr.done < length:
                    raise ConnectionError("sender stopped early")
                final = unique_path(folder, name)
                os.replace(part, final)
                if app.store.convert_heic:
                    final = heic_to_jpg(final)
                status = "received"
            except Cancelled:
                status = "cancelled"
            except (OSError, ConnectionError):
                pass
            finally:
                if status != "received":
                    try:
                        part.unlink()
                    except OSError:
                        pass
                try:
                    app.finish(tr, status, final)
                except Exception:      # bookkeeping must never turn a saved file into a failed upload
                    log.exception("after receiving %s", tr.name)
            if status == "received":
                return self.json(200, {"ok": True, "saved_as": final.name, "bytes": tr.done,
                                       "seconds": round(tr.elapsed, 3)})
            try:
                msg = b"Cancelled on the PC" if status == "cancelled" else b"Transfer failed"
                self.send(409 if status == "cancelled" else 500, msg, "text/plain", close=True)
            except OSError:
                pass

    return Handler


class Server(ThreadingHTTPServer):
    """Owns its port outright: Windows' SO_REUSEADDR would let a second DropTray share it silently."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


# ---------------------------------------------------------------- the app

class App:
    def __init__(self):
        self.store = Store()
        self.q: queue.Queue = queue.Queue()
        self.gate = Gate(self)
        self.meter = Meter()
        self.active: dict[str, Transfer] = {}
        self.batches: dict[str, Batch] = {}
        self.cancelled_tickets: set[str] = set()
        self.lock = threading.Lock()
        self.ip = lan_ip()
        self.link = "checking…"
        self.pc_clip = ""                    # latest PC clipboard text, kept by the UI thread
        self.clip = {"seq": 0, "kind": None, "text": "", "files": [], "image": ""}
        self.clip_dismissed = -1             # the drawer hides the copied-item card for this clipboard
        self._downloads: dict = {}
        self.pairing_ips: set[str] = set()
        self.ui_visible = False                      # the drawer sets this while it is on screen
        self.local_key = secrets.token_urlsafe(16)   # lets only the drawer read thumbnails
        self.zc = None
        self.mdns_info = None
        self.mdns_ok = False
        self.icon_png = CONF_DIR / "icon.png"
        make_icon(256).save(self.icon_png)
        self.notifier = Notifier(self.icon_png)
        self.server = Server(("0.0.0.0", int(self.store.port)), make_handler(self))

    @property
    def url(self) -> str:
        """For the QR code: the IP always works; the page moves itself to droptray.local when it can."""
        return f"http://{self.ip}:{self.store.port}/?pair={self.store.token}"

    @property
    def home_url(self) -> str:
        return f"http://{MDNS_HOST}:{self.store.port}/"

    def ui(self, msg):
        self.q.put(msg)

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._probe_link, daemon=True).start()
        threading.Thread(target=self._announce, daemon=True).start()

    def _probe_link(self):
        self.link = link_info(self.ip)

    def _announce(self):
        """Bonjour: answer for droptray.local and advertise _droptray._tcp, so the phone needs no IP."""
        try:
            from zeroconf import IPVersion, ServiceInfo, Zeroconf
            if self.zc is None:
                self.zc = Zeroconf(ip_version=IPVersion.V4Only)
            if self.mdns_info is not None:
                self.zc.unregister_service(self.mdns_info)
            name = re.sub(r"[^A-Za-z0-9-]", "-", self.store.pc_name)[:40] or "DropTray"
            self.mdns_info = ServiceInfo(
                "_droptray._tcp.local.", f"{name}._droptray._tcp.local.", port=int(self.store.port),
                addresses=[socket.inet_aton(self.ip)], server=f"{MDNS_HOST}.",
                properties={"id": self.store.pc_id, "name": self.store.pc_name, "path": "/",
                            "api": str(API_VERSION)})
            self.zc.register_service(self.mdns_info, allow_name_change=True)
            self.mdns_ok = True
            log.info("announced %s -> %s", MDNS_HOST, self.ip)
        except Exception as e:
            self.mdns_ok = False
            log.warning("mDNS announcement failed: %s", e)

    def check_network(self) -> bool:
        """Laptops move between home and work: follow the new address. True if it changed."""
        ip = lan_ip()
        if ip == self.ip:
            return False
        log.info("network changed: %s -> %s", self.ip, ip)
        self.ip = ip
        threading.Thread(target=self._probe_link, daemon=True).start()
        threading.Thread(target=self._announce, daemon=True).start()
        return True

    def shutdown(self):
        try:
            if self.zc:
                self.zc.unregister_all_services()
                self.zc.close()
        except Exception:
            pass
        self.store.save_conf()
        self.server.shutdown()

    # ---- batches and transfers
    def new_batch(self, files: int, nbytes: int, sender: str) -> Batch:
        b = Batch(files, nbytes, sender)
        with self.lock:
            self.batches[b.id] = b
        self.ui(("busy", None))
        return b

    def begin(self, tr: Transfer):
        with self.lock:
            self.active[tr.id] = tr
            tr.batch.touched = time.perf_counter()
        self.ui(("busy", None))

    def finish(self, tr: Transfer, status: str, path: Path | None):
        b = tr.batch
        if tr.elapsed >= 0.05:                          # too short to sample: peak is the average
            tr.peak = max(tr.peak, tr.done / tr.elapsed)
        with self.lock:
            self.active.pop(tr.id, None)
            b.touched = time.perf_counter()
            b.peak = max(b.peak, tr.peak)
            if status == "received":
                b.done_files += 1
                b.done_bytes += tr.done
                b.saved.append(path)
            else:
                b.failed += 1
        thumb = make_thumb(path) if path else ""
        rec = {"name": path.name if path else tr.name, "size": tr.done, "seconds": round(tr.elapsed, 3),
               "peak": tr.peak, "sender": tr.sender, "when": time.time(), "status": status,
               "path": str(path or ""), "kind": kind_of(path.name if path else tr.name), "thumb": thumb,
               "batch": b.id}
        self.store.add_record(rec)
        log.info("%s %s: %d bytes in %.2f s from %s", status, rec["name"], tr.done, tr.elapsed, tr.sender)
        if b.finished:
            self._batch_done(b)
        self.ui(("busy", None))

    def _batch_done(self, b: Batch):
        with self.lock:
            if self.batches.pop(b.id, None) is None:
                return
        took = max(time.perf_counter() - b.start, 1e-3)
        if not b.saved:
            if not b.cancelled:
                self.notifier.info("Transfer failed", [f"Nothing was saved from {b.sender}"])
            return
        image = next((t for t in (make_thumb(p, 256) for p in b.saved[:5]) if t), "")
        speed = fmt_speed(b.done_bytes / took)
        who = b.sender.split(" (")[0]
        n = len(b.saved)
        self.store.add_event(
            "received", f"{who} sent {b.saved[0].name if n == 1 else f'{n} files'}",
            f"{fmt_bytes(b.done_bytes)} at {speed}" + (f", {b.failed} failed" if b.failed else "")
            + (", rest cancelled" if b.cancelled else ""),
            [t for t in (make_thumb(x) for x in b.saved[:4]) if t], str(b.saved[0]) if n == 1 else "")
        folder = Path(self.store.save_dir)
        if len(b.saved) == 1:
            p = b.saved[0]
            self.notifier.info(f"Received {p.name}", [f"{fmt_bytes(b.done_bytes)} · {speed} · from {b.sender}"],
                               {"Open": lambda: os.startfile(p), "Show in folder": lambda: reveal(p)},
                               image, on_click=lambda: os.startfile(p))
        else:
            extra = f" ({b.failed} failed)" if b.failed else ""
            self.notifier.info(f"Received {len(b.saved)} files{extra}",
                               [f"{fmt_bytes(b.done_bytes)} in {took:.0f} s · {speed} · from {b.sender}"],
                               {"Open folder": lambda: os.startfile(folder)}, image,
                               on_click=lambda: self.ui(("show", "history")))

    def cancel_batch(self, batch_id: str):
        with self.lock:
            b = self.batches.get(batch_id)
            if not b:
                return
            b.cancelled = True
            for tr in self.active.values():
                if tr.batch is b:
                    tr.cancelled = True
            idle = not any(tr.batch is b for tr in self.active.values())
        for t, v in list(self.gate.tickets.items()):
            if v["batch"] is b:
                self.cancelled_tickets.add(t)
        log.info("batch %s cancelled", batch_id[:8])
        if idle:
            self._batch_done(b)
        self.ui(("busy", None))

    def cancel_ticket(self, ticket: str):
        """The phone cancelled: drop the rest of its batch."""
        v = self.gate.tickets.get(ticket)
        if v:
            self.cancel_batch(v["batch"].id)

    def ticket_cancelled(self, ticket: str) -> bool:
        return ticket in self.cancelled_tickets

    def prune_batches(self):
        """Forget batches whose phone went away mid-way (no upload for a minute)."""
        now = time.perf_counter()
        with self.lock:
            stale = [b for b in self.batches.values()
                     if now - b.touched > 60 and not any(t.batch is b for t in self.active.values())]
        for b in stale:
            b.cancelled = True
            self._batch_done(b)

    # ---- text and sharing
    def receive_text(self, text: str, device: str):
        self.store.add_text(text, device)
        is_link = bool(re.fullmatch(r"https?://\S+", text))
        self.store.add_event("text", f"{'Link' if is_link else 'Text'} from {device}", text[:200],
                             link=text if is_link else "")
        self.pc_clip = text                  # what we're about to copy shouldn't bounce back as a share
        if self.store.auto_copy:
            self.ui(("clip", text))
        url = text if re.fullmatch(r"https?://\S+", text) else ""
        buttons = {"Open link": lambda: os.startfile(url)} if url else {}
        if not self.store.auto_copy:
            buttons["Copy"] = lambda: self.ui(("clip", text))
        preview = text if len(text) < 160 else text[:157] + "…"
        self.notifier.info(f"{'Link' if url else 'Text'} from {device}" + (" · copied" if self.store.auto_copy else ""),
                           [preview], buttons)
        log.info("text from %s (%d chars)", device, len(text))

    def share_files(self, paths: list[Path]) -> int:
        items = [{"id": uuid.uuid4().hex[:12], "kind": "file", "name": p.name, "path": str(p),
                  "size": p.stat().st_size, "when": time.time()} for p in paths if p.is_file()]
        self.store.share_many(items)
        if items:
            self.store.add_event("sent", f"Shared {items[0]['name'] if len(items) == 1 else f'{len(items)} files'}"
                                 " with your phone", fmt_bytes(sum(i["size"] for i in items)),
                                 path=items[0]["path"] if len(items) == 1 else "")
        # thumbnails for the phone and the drawer, made off the caller's thread
        threading.Thread(target=lambda: [make_thumb(Path(i["path"])) for i in items], daemon=True).start()
        return len(items)

    # ---- the PC clipboard (text, copied files, or a copied image), set by the UI's poller
    def set_clip(self, seq: int, kind: str | None, text: str = "", files: list[str] | None = None,
                 image: str = ""):
        self.clip = {"seq": seq, "kind": kind, "text": text, "files": files or [], "image": image}
        if kind == "text":
            self.pc_clip = text

    def clip_public(self) -> dict:
        """What a paired phone may see of the PC clipboard: nothing unless the user turned it on."""
        c = self.clip
        if not self.store.clip_sync or not c["kind"]:
            return {"enabled": self.store.clip_sync, "kind": None, "seq": c["seq"]}
        out = {"enabled": True, "kind": c["kind"], "seq": c["seq"]}
        if c["kind"] == "text":
            out["text"] = c["text"][:MAX_TEXT]
        elif c["kind"] == "files":
            out["files"] = [{"i": i, "name": Path(f).name, "size": Path(f).stat().st_size if Path(f).is_file() else 0,
                             "type": kind_of(f), "thumb": Path(f).suffix.lower().lstrip(".") in THUMBABLE}
                            for i, f in enumerate(c["files"][:50])]
        elif c["kind"] == "image":
            out["image"] = True
        return out

    def send_clip(self) -> str:
        """Put whatever is copied on the PC into the phone's From PC list. Returns what was sent."""
        c = self.clip
        if c["kind"] == "text" and c["text"].strip():
            self.share_text(c["text"])
            return "text"
        if c["kind"] == "files":
            n = self.share_files([Path(f) for f in c["files"]])
            return f"{n} file{'s' if n != 1 else ''}"
        if c["kind"] == "image" and c["image"]:
            sent = CONF_DIR / "sent"
            sent.mkdir(exist_ok=True)
            target = unique_path(sent, time.strftime("Copied image %Y-%m-%d %H.%M.%S.png"))
            shutil.copyfile(c["image"], target)
            self.share_files([target])
            return "image"
        return ""

    def share_text(self, text: str):
        self.store.share({"id": uuid.uuid4().hex[:12], "kind": "text", "name": "Text", "text": text[:MAX_TEXT],
                          "size": len(text), "when": time.time()})
        self.store.add_event("sent", "Shared text with your phone", text[:200])

    def note_download(self, item: dict, device: str):
        """The phone saved something the PC shared; once per item per two minutes."""
        key = (item["id"], device)
        now = time.time()
        if now - self._downloads.get(key, 0) < 120:
            return
        self._downloads[key] = now
        self.store.add_event("downloaded", f"{device} saved {item['name']}", fmt_bytes(item.get("size", 0)),
                             path=item.get("path", ""))

    def dismiss_clip(self):
        self.clip_dismissed = self.clip["seq"]


def reveal(path: Path):
    subprocess.Popen(["explorer", "/select,", str(path)])
