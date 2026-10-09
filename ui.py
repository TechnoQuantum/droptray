"""DropTray's popover: a WebView2 page over Windows 11 acrylic, anchored above the tray icon.

Python owns the state and the actions; web/drawer.html renders them and polls `Api.state()`.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
import io
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

import pystray
import qrcode
from PIL import ImageGrab
import webview
from webview.dom import DOMEventHandler

import updater
from core import (APP, APP_VERSION, HERE, App, fmt_bytes, fmt_eta, fmt_speed, kind_of, log, make_icon, make_thumb, reveal)

WIDTH, HEIGHT = 372, 580                      # logical pixels
WIDGET = 64                                   # the desktop tile, logical pixels
CLIP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP / "clip"   # a copied image, as PNG

user32, kernel32, dwmapi = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.dwmapi
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.OpenClipboard.argtypes = [ctypes.c_void_p]
user32.GetClipboardSequenceNumber.restype = ctypes.c_uint
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
CF_UNICODETEXT = 13


# ---------------------------------------------------------------- Windows helpers

def clip_get() -> str | None:
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT) or not user32.OpenClipboard(None):
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else None
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clip_set(text: str) -> bool:
    data = text.encode("utf-16-le") + b"\0\0"
    h = kernel32.GlobalAlloc(0x0002, len(data))          # GMEM_MOVEABLE
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    for _ in range(20):                                   # another app may hold the clipboard briefly
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        return bool(user32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        user32.CloseClipboard()


def windows_dark_mode() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    """How Windows should start DropTray at sign-in: the packaged exe, or pythonw with the script."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{Path(sys.executable).with_name("pythonw.exe")}" "{HERE / "droptray.py"}"'


def startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def set_startup(on: bool):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


def recycle(path: Path):
    cmd = ("Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
           f"'{str(path).replace(chr(39), chr(39) * 2)}', 'OnlyErrorDialogs', 'SendToRecycleBin')")
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd], creationflags=subprocess.CREATE_NO_WINDOW)


def day_label(ts: float) -> str:
    d, today = time.localtime(ts), time.localtime()
    if (d.tm_year, d.tm_yday) == (today.tm_year, today.tm_yday):
        return "Today"
    y = time.localtime(time.time() - 86400)
    if (d.tm_year, d.tm_yday) == (y.tm_year, y.tm_yday):
        return "Yesterday"
    return time.strftime("%A %d %B", d).replace(" 0", " ")


THUMB_BASE = ""      # set by Drawer: the local thumbnail URL prefix with this session's key


def file_url(path: str) -> str:
    """Thumbnail URL the page can load (it is served over http, so file:// images are blocked)."""
    if not path:
        return ""
    name, key = Path(path).name, THUMB_BASE
    return f"{key[0]}{name}?k={key[1]}" if key else ""


def ago(ts: float) -> str:
    s = time.time() - ts
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60:.0f} min ago"
    if s < 86400:
        return f"{s // 3600:.0f} h ago"
    return time.strftime("%d %b", time.localtime(ts))


# ---------------------------------------------------------------- the bridge the page calls

class Api:
    """Every public method here is callable from the page as `pywebview.api.<name>(...)`."""

    def __init__(self, drawer: "Drawer"):
        self._d = drawer

    # ---- reads
    def state(self) -> dict:
        d, app = self._d, self._d.app
        st, gate = app.store, app.gate
        with gate.lock:
            pending = list(gate.pending.values())
        with app.lock:
            batches = list(app.batches.values())
            active = list(app.active.values())
        speed = app.meter.speed

        if gate.paused:
            status = {"text": "Paused", "tone": "warn"}
        elif pending:
            status = {"text": "Waiting for you", "tone": "warn"}
        elif active:
            status = {"text": f"Receiving at {fmt_speed(speed)}", "tone": "busy"}
        else:
            mode = ("accepts everything" if st.auto_accept else
                    f"allowing for {int(gate.trust_until - time.time())} s" if time.time() < gate.trust_until
                    else "asks before receiving")
            status = {"text": f"Ready, {mode}", "tone": "ok"}

        out_batches = []
        for b in batches:
            mine = [t for t in active if t.batch is b]
            done = b.done_bytes + sum(t.done for t in mine)
            frac = done / b.bytes if b.bytes else (b.done_files / b.files if b.files else 0)
            out_batches.append({
                "id": b.id, "files": b.files, "index": min(b.done_files + b.failed + max(len(mine), 1), b.files),
                "done": fmt_bytes(done), "total": fmt_bytes(b.bytes) if b.bytes else "", "frac": min(frac, 1),
                "speed": fmt_speed(speed), "eta": fmt_eta((b.bytes - done) / speed) if speed > 0 and b.bytes else "",
                "current": mine[0].name if mine else "", "sender": b.sender.split(" (")[0],
            })

        t = st.totals
        with st.lock:
            recent = [r for r in reversed(st.records) if r["status"] == "received"][:3]
            devices = list(st.devices)
            counts = {"shared": len(st.shared), "texts": len(st.texts)}
        sp = st.speedtest
        return {
            "status": status, "paused": gate.paused, "busy": bool(active or batches),
            "pc": {"name": st.pc_name, "ip": app.ip, "port": st.port, "link": app.link, "mdns": app.mdns_ok},
            "settings": {"auto_accept": st.auto_accept, "convert_heic": st.convert_heic, "auto_copy": st.auto_copy,
                         "clip_sync": st.clip_sync, "keep_open": st.keep_open, "startup": d.startup,
                         "widget": st.widget, "drawer_at": st.drawer_at, "glass": st.glass,
                         "save_dir": st.save_dir, "token": st.token, "blocks": st.blocks},
            "prompts": [{"id": p.id, "title": p.title, "lines": p.lines, "sender": p.sender.split(" (")[0],
                         "files": [{"name": n, "kind": kind_of(n)} for n in p.files[:9]],
                         "more": max(len(p.files) - 9, 0), "pairing": p.pairing} for p in pending],
            "batches": out_batches,
            "speed": fmt_speed(speed) if active else "",
            "samples": [round(x) for x in list(app.meter.samples)[-90:]],
            "totals": {"files": f"{t['files']:,}", "bytes": fmt_bytes(t["bytes"]),
                       "avg": fmt_speed(t["bytes"] / t["seconds"]) if t["seconds"] else "–",
                       "peak": fmt_speed(t["peak"])},
            "speedtest": None if not sp else {"up": f"{sp['up_mbps']:.0f}", "down": f"{sp['down_mbps']:.0f}",
                                              "ping": f"{sp['ping_ms']:.0f}", "when": ago(sp["when"])},
            "recent": [{"name": r["name"], "kind": r.get("kind", kind_of(r["name"])), "thumb": file_url(r.get("thumb", "")),
                        "path": r["path"], "sub": f"{fmt_bytes(r['size'])}, {ago(r['when'])}"} for r in recent],
            "devices": [{"id": x["id"], "name": x["name"], "online": time.time() - x.get("last_seen", 0) < 20,
                         "sub": ("online" if time.time() - x.get("last_seen", 0) < 20
                                 else f"seen {ago(x.get('last_seen', 0))}")} for x in devices],
            "counts": counts, "version": st.version,
            "clip": self._clip_summary(), "today": self._today(), "events": self._events(),
            "update": self._d.updater.status(),
        }

    def _today(self) -> str:
        n, size = self._d.app.store.today()
        return f"{n} file{'s' if n != 1 else ''}, {fmt_bytes(size)} received today" if n else "Nothing received yet today"

    def _events(self) -> list:
        st = self._d.app.store
        with st.lock:
            evs = list(reversed(st.events[-12:]))
        return [{"id": e["id"], "kind": e["kind"], "title": e["title"], "sub": e["sub"], "ago": ago(e["when"]),
                 "thumbs": [file_url(t) for t in e.get("thumbs", []) if os.path.exists(t)],
                 "path": e["path"] if e.get("path") and os.path.exists(e["path"]) else "", "link": e.get("link", "")}
                for e in evs]

    def dismiss_clip(self):
        self._d.app.dismiss_clip()

    def update_check(self):
        self._d.updater.check_now()

    def update_install(self):
        self._d.updater.install_now()

    def _clip_summary(self) -> dict | None:
        c = self._d.app.clip
        if c["seq"] == self._d.app.clip_dismissed:
            return None
        if c["kind"] == "text":
            first = c["text"].strip().splitlines()[0] if c["text"].strip() else ""
            return {"seq": c["seq"], "kind": "text", "label": first[:120],
                    "sub": f"Text, {len(c['text']):,} characters", "thumbs": []}
        if c["kind"] == "files":
            files = c["files"]
            label = Path(files[0]).name if len(files) == 1 else f"{len(files)} files"
            size = sum(os.path.getsize(f) for f in files[:200] if os.path.isfile(f))
            names = ", ".join(Path(f).name for f in files[1:3]) if len(files) > 1 else ""
            return {"seq": c["seq"], "kind": "files", "label": label,
                    "sub": fmt_bytes(size) + (f", {Path(files[0]).name}, {names}" if names else ""),
                    "thumbs": [{"kind": kind_of(f), "name": Path(f).name,
                                "thumb": file_url(make_thumb(Path(f), cached_only=True))} for f in files[:4]]}
        if c["kind"] == "image":
            return {"seq": c["seq"], "kind": "image", "label": "Copied image", "sub": "Screenshot or picture",
                    "thumbs": [{"kind": "image", "name": "image.png",
                                "thumb": file_url(make_thumb(Path(c["image"]), cached_only=True))}]}
        return None

    def send_clip(self) -> str:
        return self._d.app.send_clip()

    def history(self) -> list:
        st = self._d.app.store
        with st.lock:
            recs = list(reversed(st.records))
        out = []
        for r in recs:
            path = r.get("path") or ""
            exists = bool(path) and os.path.exists(path)
            if r["status"] == "received":
                sub = f"{fmt_bytes(r['size'])} at {fmt_speed(r['size'] / max(r['seconds'], 1e-3))}"
                if not exists:
                    sub = "Moved or deleted"
            else:
                sub = r["status"].capitalize()
            out.append({"name": r["name"], "kind": r.get("kind") or kind_of(r["name"]), "status": r["status"],
                        "path": path if exists else "", "thumb": file_url(r.get("thumb", "")) if exists else "",
                        "sub": sub, "time": time.strftime("%H:%M", time.localtime(r["when"])),
                        "day": day_label(r["when"]), "from": r.get("sender", "").split(" (")[0]})
        return out

    def phone(self) -> dict:
        st = self._d.app.store
        with st.lock:
            shared = list(reversed(st.shared))
            texts = list(reversed(st.texts))
        out_shared = []
        for s in shared:
            if s["kind"] == "file":
                p = Path(s["path"])
                out_shared.append({"id": s["id"], "kind": "file", "name": s["name"], "ftype": kind_of(s["name"]),
                                   "thumb": file_url(make_thumb(p, cached_only=True)) if p.exists() else "",
                                   "sub": f"{fmt_bytes(s['size'])}, {ago(s['when'])}"})
            else:
                out_shared.append({"id": s["id"], "kind": "text", "name": s["text"][:300],
                                   "sub": f"Text, {ago(s['when'])}"})
        return {"shared": out_shared,
                "texts": [{"id": t["id"], "text": t["text"][:2000], "link": bool(re.fullmatch(r"https?://\S+", t["text"])),
                           "sub": f"{t['device']}, {ago(t['when'])}"} for t in texts[:40]]}

    def pair_info(self) -> dict:
        app = self._d.app
        img = qrcode.make(app.url, border=1, box_size=8).get_image()
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return {"qr": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
                "home": app.home_url.replace("http://", "").rstrip("/"), "ip": f"{app.ip}:{app.store.port}",
                "mdns": app.mdns_ok, "code": app.store.token, "link": app.url}

    # ---- transfers
    def decide(self, prompt_id: str, choice: str):
        with self._d.app.gate.lock:
            p = self._d.app.gate.pending.get(prompt_id)
        if p:
            p.decide(choice)

    def cancel_batch(self, batch_id: str):
        self._d.app.cancel_batch(batch_id)

    def pause(self):
        self._d.app.gate.paused = not self._d.app.gate.paused

    # ---- files
    def open_path(self, path: str):
        if path and os.path.exists(path):
            os.startfile(path)

    def reveal(self, path: str):
        if path and os.path.exists(path):
            reveal(Path(path))

    def open_folder(self):
        p = Path(self._d.app.store.save_dir)
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)

    def copy_text(self, text: str):
        self._d.copy(text)

    def recycle(self, path: str):
        if path and os.path.exists(path):
            recycle(Path(path))
            self._d.app.store.version += 1

    def share_path(self, path: str):
        if path and os.path.exists(path):
            self._d.app.share_files([Path(path)])

    def open_url(self, url: str):
        if re.fullmatch(r"https?://\S+", url or ""):
            os.startfile(url)

    # ---- phone
    def pick_share(self) -> int:
        files = self._d.dialog(webview.FileDialog.OPEN, allow_multiple=True)
        return self._d.app.share_files([Path(f) for f in files or []])

    def share_clipboard(self) -> bool:
        text = clip_get() or ""
        if text.strip():
            self._d.app.share_text(text)
            return True
        return False

    def unshare(self, item_id: str):
        self._d.app.store.unshare(item_id)

    def clear_shared(self):
        st = self._d.app.store
        with st.lock:
            st.shared = []
        st.save_history()

    def unpair(self, dev_id: str):
        self._d.app.store.remove_device(dev_id)

    # ---- settings
    def set_setting(self, key: str, value):
        st = self._d.app.store
        if key in ("auto_accept", "convert_heic", "auto_copy", "clip_sync", "keep_open", "auto_update"):
            setattr(st, key, bool(value))
            if key == "clip_sync":
                self._d.clip_last = self._d.app.pc_clip     # start with the next copy, not the current one
            st.save_conf()
        elif key == "glass":
            if value in BACKDROPS:
                st.glass = MATERIAL["value"] = value
                st.save_conf()
                for w in (self._d.window, self._d.widget):
                    if w is not None:
                        set_material(w, value)
        elif key == "drawer_at":
            if value in ("tray", "widget"):
                self._d.app.store.drawer_at = value
                self._d.app.store.save_conf()
        elif key == "widget":
            self._d.app.store.widget = bool(value)
            self._d.app.store.save_conf()
            self._d.set_widget(bool(value))
        elif key == "startup":
            set_startup(bool(value))
            self._d.startup = startup_enabled()
        elif key == "block":
            name, mode = value
            if name in st.BLOCK_DEFAULTS and mode in ("always", "busy", "idle", "off"):
                st.blocks[name] = mode
                st.save_conf()
        elif key == "pc_name":
            name = str(value).strip()[:40]
            if name:
                st.pc_name = name
                st.save_conf()
                threading.Thread(target=self._d.app._announce, daemon=True).start()
        elif key == "token":
            code = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
            if not 4 <= len(code) <= 12:
                return "Use 4 to 12 letters or digits."
            st.token = code
            st.save_conf()
        return ""

    def new_code(self) -> str:
        st = self._d.app.store
        st.token = secrets.token_hex(3).upper()
        st.save_conf()
        return st.token

    def choose_folder(self) -> str:
        res = self._d.dialog(webview.FileDialog.FOLDER, directory=self._d.app.store.save_dir)
        if res:
            self._d.app.store.save_dir = str(Path(res[0]))
            self._d.app.store.save_conf()
        return self._d.app.store.save_dir

    def clear_history(self):
        st = self._d.app.store
        with st.lock:
            st.records.clear()
            st.totals = {"files": 0, "bytes": 0, "seconds": 0.0, "peak": 0.0}
        st.save_history()

    # ---- window
    def hide(self):
        self._d.hide()

    def blurred(self):
        self._d.blurred()

    def quit(self):
        self._d.quit()


# ---------------------------------------------------------------- glass, shared by the drawer and the widget

class MARGINS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


BACKDROPS = {"frosted": 3, "clear": 1, "mica": 2}      # DWMWA_SYSTEMBACKDROP_TYPE values
MATERIAL = {"value": "frosted"}                       # set from the store at startup and by Settings


def set_material(window, kind: str):
    """Switch a glass window's material without recreating it."""
    try:
        hwnd = window.native.Handle.ToInt32()
        dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(ctypes.c_int(BACKDROPS.get(kind, 3))), 4)
    except Exception as e:
        log.info("could not change the window material: %s", e)


def glass(window) -> bool:
    """Windows 11 acrylic behind a transparent page: the macOS-style frosted backdrop."""
    try:
        import System
        from System.Drawing import Color
        form = window.native

        def apply():
            # Order matters: WinForms recreates the window when ShowInTaskbar changes, so the
            # backdrop and styles go on the new handle afterwards. All of it runs on the UI thread.
            form.BackColor = Color.Black
            form.ShowInTaskbar = False
            hwnd = form.Handle.ToInt32()
            one = lambda v: ctypes.byref(ctypes.c_int(v))
            dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(-1, -1, -1, -1)))
            dwmapi.DwmSetWindowAttribute(hwnd, 20, one(1 if windows_dark_mode() else 0), 4)   # dark frame
            dwmapi.DwmSetWindowAttribute(hwnd, 33, one(2), 4)                                 # round corners
            dwmapi.DwmSetWindowAttribute(hwnd, 38, one(BACKDROPS.get(MATERIAL["value"], 3)), 4)   # the material
            ex = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, (ex | 0x80) & ~0x40000)   # tool window: no taskbar button
        # BeginInvoke, not Invoke: this can run inside a pywebview event the UI thread is still dispatching
        form.BeginInvoke(System.Action(apply))
        return True
    except Exception as e:
        log.warning("acrylic backdrop unavailable: %s", e)
        return False


def window_rect(hwnd: int) -> wt.RECT:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


class WidgetApi:
    """What the desktop tile's page may call."""

    def __init__(self, drawer: "Drawer"):
        self._d = drawer

    def widget_state(self) -> dict:
        app = self._d.app
        with app.lock:
            batches = list(app.batches.values())
            active = list(app.active.values())
        frac = 0.0
        if batches:
            b = batches[0]
            done = b.done_bytes + sum(t.done for t in active if t.batch is b)
            frac = min(done / b.bytes, 1) if b.bytes else 0.0
        pending = len(app.gate.pending)
        state = ("paused" if app.gate.paused else "ask" if pending else "busy" if active or batches else "idle")
        tip = {"paused": "DropTray: paused", "ask": "DropTray: waiting for you",
               "busy": f"DropTray: receiving, {frac:.0%}", "idle": "DropTray: ready"}[state]
        return {"state": state, "frac": round(frac, 3), "pending": pending, "tip": tip}

    def widget_press(self):
        self._d.widget_press()


# ---------------------------------------------------------------- the popover window and the tray

class Drawer:
    def __init__(self, app: App):
        global THUMB_BASE
        self.app = app
        MATERIAL["value"] = app.store.glass if app.store.glass in BACKDROPS else "frosted"
        THUMB_BASE = (f"http://127.0.0.1:{app.store.port}/local/thumb/", app.local_key)
        self.visible = False
        self.shown_at = 0.0
        self.hidden_at = 0.0
        self.in_dialog = False
        self.blur_watch = False
        self.glass_done = False
        self.startup = startup_enabled()
        if self.startup:
            set_startup(True)                  # keep the entry pointing at this copy (it may have moved)
        self.clip_last = ""
        self.tray: pystray.Icon | None = None
        self._tray_state = None
        self.widget = None
        self.updater = updater.Updater(app, restart=self.quit)
        self.window = webview.create_window(
            APP, url=str(HERE / "web" / "drawer.html"), js_api=Api(self), width=WIDTH, height=HEIGHT,
            frameless=True, transparent=True, resizable=False, on_top=True, easy_drag=False, x=-4000, y=-4000,
            background_color="#000000")
        self.window.events.loaded += self._on_loaded
        self.window.events.closing += self._on_closing
        self.quitting = False

    # ---- window
    @property
    def hwnd(self) -> int:
        return self.window.native.Handle.ToInt32()

    def _glass(self):
        if not self.glass_done:
            self.glass_done = glass(self.window)

    def _place(self, anchor: wt.RECT | None = None):
        """Bottom-right corner of the work area, just above the tray; or beside the widget."""
        hwnd = self.hwnd
        rect = wt.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)          # work area, physical px
        scale = (user32.GetDpiForWindow(hwnd) or 96) / 96
        if anchor is not None:
            w, h, gap = WIDTH * scale, HEIGHT * scale, 10 * scale
            x = anchor.left - w - gap if anchor.left - w - gap >= rect.left else anchor.right + gap
            y = min(max(anchor.top, rect.top + gap), rect.bottom - h - gap)
            self.window.resize(WIDTH, HEIGHT)
            self.window.move(int(x / scale), int(y / scale))
            return
        # through pywebview, so WinForms' own idea of the location changes too (it reasserts it on show)
        self.window.resize(WIDTH, HEIGHT)
        self.window.move(int(rect.right / scale) - WIDTH - 12, int(rect.bottom / scale) - HEIGHT - 12)

    def show(self, tab: str | None = None, anchor: wt.RECT | None = None):
        try:
            self._show(tab, anchor)
        except Exception:
            import traceback
            log.error("show failed: %s", traceback.format_exc())

    def _show(self, tab: str | None = None, anchor: wt.RECT | None = None):
        self._glass()
        if anchor is None and self.app.store.drawer_at == "widget" and self.widget is not None and self.app.store.widget:
            anchor = window_rect(self.widget.native.Handle.ToInt32())
        self.window.show()
        for _ in range(40):                       # show() lands on the UI thread a moment later
            if user32.IsWindowVisible(self.hwnd):
                break
            time.sleep(0.01)
        self._place(anchor)
        user32.SetForegroundWindow(self.hwnd)
        self.visible = self.app.ui_visible = True
        self.shown_at = time.monotonic()
        self.window.evaluate_js(f"onShow({tab!r})" if tab else "onShow(null)")

    def hide(self):
        if self.visible:
            # tell the page without waiting for it: the UI thread may be busy with the very close that led here
            threading.Thread(target=lambda: self.window.evaluate_js("onHide()"), daemon=True).start()
            self.window.hide()
            self.visible = self.app.ui_visible = False
            self.hidden_at = time.monotonic()

    def toggle(self):
        if self.visible:
            self.hide()
        elif time.monotonic() - self.hidden_at > 0.4:   # the tray click itself just closed it via blur
            self.show()

    def blurred(self):
        """Clicking elsewhere closes the popover, like a macOS menu bar extra..."""
        if self.app.store.keep_open or self.in_dialog or time.monotonic() - self.shown_at < 0.5:
            return
        if not self.blur_watch:
            self.blur_watch = True
            threading.Thread(target=self._watch_blur, daemon=True).start()

    def _watch_blur(self):
        """...except while a mouse button is held: dragging a file from Explorer starts with a click
        there. Decide on release: over the drawer (a drop) keeps it open, anywhere else closes it."""
        try:
            while self.visible:
                hwnd = self.hwnd
                if user32.GetForegroundWindow() == hwnd:
                    return
                if user32.GetAsyncKeyState(0x01) & 0x8000 or user32.GetAsyncKeyState(0x02) & 0x8000:
                    time.sleep(0.04)
                    continue
                pt, r = wt.POINT(), wt.RECT()
                user32.GetCursorPos(ctypes.byref(pt))
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                if not (r.left <= pt.x < r.right and r.top <= pt.y < r.bottom):
                    self.hide()
                    return
                time.sleep(0.04)       # released over the drawer: stay, and watch for the next click
        finally:
            self.blur_watch = False

    def dialog(self, kind, **kw):
        self.in_dialog = True
        try:
            return self.window.create_file_dialog(kind, **kw)
        finally:
            self.in_dialog = False
            user32.SetForegroundWindow(self.hwnd)

    # ---- desktop widget
    def set_widget(self, on: bool):
        if on and self.widget is None:
            x, y = self.app.store.widget_pos or self._widget_default()
            self.widget = webview.create_window(
                "DropTray widget", url=str(HERE / "web" / "widget.html"), js_api=WidgetApi(self), width=WIDGET,
                height=WIDGET, x=int(x), y=int(y), frameless=True, transparent=True, resizable=False, on_top=False,
                focus=False, easy_drag=False, background_color="#000000", min_size=(WIDGET, WIDGET))
            self.widget.events.loaded += self._widget_loaded
        elif on:
            self.widget.show()
            self._widget_bottom()
        elif self.widget is not None:
            self.widget.hide()

    def _widget_loaded(self):
        glass(self.widget)
        hwnd = self.widget.native.Handle.ToInt32()
        size = int(WIDGET * (user32.GetDpiForWindow(hwnd) or 96) / 96)
        # WinForms keeps borderless windows at least ~120 px wide; set the real size underneath it
        user32.SetWindowPos(hwnd, 1, 0, 0, size, size, 0x0002 | 0x0010)
        self._widget_bottom()

    def _widget_default(self) -> tuple[int, int]:
        rect = wt.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)
        scale = (user32.GetDpiForSystem() or 96) / 96
        return int(rect.right / scale) - WIDGET - 28, int(rect.top / scale) + 28

    def _widget_bottom(self):
        """Below every window: the tile is seen on the desktop and never over an app or a game."""
        if self.widget is not None and self.app.store.widget:
            try:
                user32.SetWindowPos(self.widget.native.Handle.ToInt32(), 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            except Exception:
                pass

    def widget_press(self):
        """A press on the tile: drag it while the button is held, otherwise it's a click."""
        hwnd = self.widget.native.Handle.ToInt32()
        before = window_rect(hwnd)
        if user32.GetAsyncKeyState(0x01) & 0x8000:
            import System
            form = self.widget.native

            def drag():
                user32.ReleaseCapture()
                user32.SendMessageW(hwnd, 0xA1, 2, 0)        # WM_NCLBUTTONDOWN on the caption: Windows moves it
            form.Invoke(System.Action(drag))                  # the move loop must run on the window's thread
        after = window_rect(hwnd)
        if abs(after.left - before.left) + abs(after.top - before.top) > 4:
            scale = (user32.GetDpiForWindow(hwnd) or 96) / 96
            self.app.store.widget_pos = [round(after.left / scale), round(after.top / scale)]
            self.app.store.save_conf()
            self._widget_bottom()
            return
        self._widget_bottom()
        if self.visible:
            self.hide()
        elif time.monotonic() - self.hidden_at > 0.4:
            self.show(anchor=after)

    def copy(self, text: str):
        clip_set(text)
        self.app.pc_clip = text
        self.clip_last = text

    def _on_loaded(self):
        try:
            self._glass()
            if not self.visible:                  # created on-screen-capable but off-screen; now tuck it away
                self.window.hide()
        except Exception:
            import traceback
            log.error("on_loaded failed: %s", traceback.format_exc())

        def on_drop(e):
            files = (e.get("dataTransfer") or {}).get("files") or []
            paths = [Path(f["pywebviewFullPath"]) for f in files if f.get("pywebviewFullPath")]
            n = self.app.share_files(paths)
            self.window.evaluate_js(f"onDropped({n})")
        # Only the drop crosses into Python; dragover is prevented in the page itself. A Python
        # dragover handler serialised every event (dozens a second) and froze the window.
        self.window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        if "--show" in sys.argv or any(a.startswith("--tab=") for a in sys.argv):
            tab = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--tab=")), None)
            threading.Timer(0.4, lambda: self.show(tab)).start()

    # ---- tray
    def _start_tray(self):
        def item(text, fn, **kw):
            return pystray.MenuItem(text, lambda icon, it: self.app.ui(("call", fn)), **kw)

        store = self.app.store
        menu = pystray.Menu(
            item("Open DropTray", self.toggle, default=True),
            pystray.Menu.SEPARATOR,
            item("Pause receiving", lambda: setattr(self.app.gate, "paused", not self.app.gate.paused),
                 checked=lambda i: self.app.gate.paused),
            item("Accept everything", lambda: (setattr(store, "auto_accept", not store.auto_accept), store.save_conf()),
                 checked=lambda i: store.auto_accept),
            item("Send copied item to phone", lambda: self.app.send_clip()),
            item("Open received folder", lambda: Api(self).open_folder()),
            item("Check for updates", lambda: (self.updater.check_now(), self.show("settings"))),
            pystray.Menu.SEPARATOR,
            item("Quit", self.quit),
        )
        self.tray = pystray.Icon(APP, make_icon(64), APP, menu)
        self.tray.run_detached()

    def _set_tray(self):
        app = self.app
        if app.gate.paused:
            state, tip = "paused", f"{APP}: paused"
        elif app.gate.pending:
            state, tip = "ask", f"{APP}: waiting for you to accept"
        elif app.active:
            state, tip = "busy", f"{APP}: receiving at {fmt_speed(app.meter.speed)}"
        else:
            state, tip = "idle", f"{APP}: ready on {app.ip}"
        if state != self._tray_state:
            self.tray.icon = make_icon(64, state)
            self._tray_state = state
        self.tray.title = tip[:127]

    # ---- background loop
    def _loop(self):
        last_clip = last_prune = last_bottom = 0.0
        while True:
            try:
                kind, val = self.app.q.get(timeout=0.25)
                if kind == "show":
                    self.show(val if isinstance(val, str) else ("activity" if self.app.gate.pending else None))
                elif kind == "call":
                    val()
                elif kind == "clip":
                    self.copy(val)
                continue
            except queue.Empty:
                pass
            except Exception as e:
                log.warning("ui loop: %s", e)
            now = time.monotonic()
            self.app.meter.sample(bool(self.app.active))
            if now - last_clip > 1.0:
                last_clip = now
                self._poll_clipboard()
            if now - last_prune > 10:
                last_prune = now
                self.app.prune_batches()
                self.app.check_network()
            if self.tray:
                self._set_tray()
            if self.widget is not None and now - last_bottom > 0.5:
                last_bottom = now
                self._widget_bottom()

    def _poll_clipboard(self):
        """Track what is copied on the PC. The sequence number makes the idle check nearly free."""
        seq = user32.GetClipboardSequenceNumber()
        if seq == self.app.clip["seq"]:
            return
        try:
            if user32.IsClipboardFormatAvailable(15):                    # CF_HDROP: files copied in Explorer
                files = ImageGrab.grabclipboard()
                files = [f for f in files if os.path.isfile(f)] if isinstance(files, list) else []
                self.app.set_clip(seq, "files" if files else None, files=files)
                threading.Thread(target=lambda: [make_thumb(Path(f)) for f in files[:8]], daemon=True).start()
            elif user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                text = clip_get() or ""
                self.app.set_clip(seq, "text" if text.strip() else None, text=text)
            elif user32.IsClipboardFormatAvailable(8) or user32.IsClipboardFormatAvailable(17):   # a bitmap
                img = ImageGrab.grabclipboard()
                if img is None:
                    self.app.set_clip(seq, None)
                    return
                CLIP_DIR.mkdir(parents=True, exist_ok=True)
                out = CLIP_DIR / f"copied-{seq}.png"
                for old in CLIP_DIR.glob("copied-*.png"):
                    old.unlink(missing_ok=True)
                img.save(out)
                self.app.set_clip(seq, "image", image=str(out))
                make_thumb(out)
            else:
                self.app.set_clip(seq, None)
        except Exception as e:
            log.info("clipboard read failed: %s", e)
            self.app.set_clip(seq, None)

    def _on_closing(self):
        """Closing the drawer (Alt+F4, or a stray taskbar entry) only hides it; Quit is the way out."""
        log.info("drawer close requested (quitting=%s, visible=%s)", self.quitting, self.visible)
        if self.quitting:
            return True
        threading.Thread(target=self.hide, daemon=True).start()
        return False

    def quit(self):
        self.quitting = True
        for p in list(self.app.gate.pending.values()):
            p.decide("decline")
        if self.tray:
            self.tray.stop()
        self.app.shutdown()
        for w in (self.widget, self.window):
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass

    def run(self):
        self._poll_clipboard()
        self._start_tray()
        updater.note_updated(self.app)
        self.updater.start()
        if self.app.store.widget:
            self.set_widget(True)
        threading.Thread(target=self._loop, daemon=True).start()
        webview.start(private_mode=False, storage_path=str(Path(os.environ["APPDATA"]) / APP / "webview"))
        # The window loop is over (Quit, or the window was destroyed some other way). Never linger as a
        # half-alive tray icon: release the port and the tray, then end the process.
        log.info("window loop ended; exiting")
        try:
            if self.tray:
                self.tray.stop()
            self.app.shutdown()
        finally:
            os._exit(0)
