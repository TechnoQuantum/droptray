"""DropTray: move files, text and the clipboard between an iPhone and this PC, from the tray.

The phone sends through a web page this app serves (pair once by QR code) or
through iOS Shortcuts. Every incoming transfer asks first with an Accept/Decline
notification, unless "Ask before receiving" is off.
"""
import ctypes

from core import APP, AUMID, CONF_DIR, DEFAULT_PORT, App, log


def saved_port() -> int:
    import json
    try:
        return int(json.loads((CONF_DIR / "config.json").read_text("utf-8")).get("port", DEFAULT_PORT))
    except (OSError, ValueError, TypeError):
        return DEFAULT_PORT


def already_running() -> bool:
    """If DropTray already runs, ask it to open its drawer and stop here."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{saved_port()}/local/show", timeout=1.5) as r:
            return json.load(r).get("app") == APP
    except Exception:
        return False


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    if already_running():
        return
    try:
        app = App()
    except OSError as e:
        log.error("could not start: %s", e)
        ctypes.windll.user32.MessageBoxW(
            None, f"Could not start the receiver: no free network port.\n\n{e}", APP, 0x10)
        return
    from ui import Drawer
    app.start()
    Drawer(app).run()


if __name__ == "__main__":
    main()
