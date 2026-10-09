# DropTray

Move files, text and the clipboard between an iPhone and Windows over the local network,
from the system tray. Every incoming transfer asks first (Accept / Allow 5 min / Decline)
as a Windows notification and in the drawer, even while DropTray is minimized.

## Install

Download **DropTraySetup-x.y.z.exe** from [Releases](https://github.com/TechnoQuantum/droptray/releases)
and run it. No administrator rights are needed, except for the optional firewall step. That step
opens TCP 8765 and UDP 5353 on private networks so your phone can reach the PC.

Requires Windows 11 (the glass uses its Acrylic backdrop) and the WebView2 runtime, which Windows 11 includes.

- Start Menu → **DropTray**. Click the tray icon, or the desktop widget if it's on, to open the drawer.
  Starting it again while it runs just opens the drawer.
- Settings (gear): **Open at sign-in**, **Desktop widget**, **Open the drawer** above the tray or
  next to the widget, and **Window glass** (Frosted, Clear or Mica).
- Uninstall from Settings → Apps. Settings and history stay in `%APPDATA%\DropTray`.

## Build from source

```powershell
py -3.11 -m venv .venv
.venv\Scripts\pip install pywebview pystray Pillow pillow-heif windows-toasts qrcode zeroconf pyinstaller
.venv\Scripts\python.exe droptray.py --show                          # run from source
.venv\Scripts\python.exe -m PyInstaller DropTray.spec --noconfirm    # -> dist\DropTray\DropTray.exe
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\DropTray.iss   # -> installer\Output\DropTraySetup-*.exe
```

`install.ps1` installs a `dist` build without Inno Setup; `DropTray.vbs` runs from source without a console.

## The drawer

A glass popover above the tray (Windows 11 acrylic behind a WebView2 page). It closes when
you click elsewhere, like a macOS menu bar extra; Settings → *Stay open* turns that off.

| Tab | What's there |
|---|---|
| Activity | The lens: ready, a request waiting for you, or a batch filling with progress. Below it, blocks you choose |
| History | Search, filters, thumbnails; click to open, folder button, right-click for more. Only this list scrolls |
| Phone | Paired devices across the top, then *To phone* (drop files, send clipboard) and *From phone* (text and links) |
| Settings (gear) | Ask before receiving, HEIC to JPG, save folder, clipboard, Activity blocks (Always / While receiving / When idle / Off), PC name, pairing code, open at sign-in |

Tray icon colour: indigo ready · amber waiting for you · green receiving · grey paused.

## iPhone

See [SHORTCUT.md](SHORTCUT.md). In short: scan the QR code, then add `droptray.local:8765`
to the Home Screen. One icon works with every paired PC (home, work), whichever is on
the current network.

## Files

| | |
|---|---|
| `droptray.py` | entry point |
| `core.py` | receiver, pairing, accept prompts, notifications, Bonjour |
| `ui.py` | tray icon, glass window, and the bridge the drawer page calls |
| `web/drawer.html` | the drawer |
| `web/index.html` | the phone page |
| `installer/DropTray.iss` | the Inno Setup installer script |
| `API.md` | the HTTP API, for the phone page today and a native iOS app later |
| `%APPDATA%\DropTray\` | `config.json` (settings, paired devices), `history.json`, `droptray.log`, thumbnails |
| `Downloads\DropTray` | received files (changeable) |

Ports: TCP 8765 (transfers), UDP 5353 (Bonjour), private networks only.

## Tests

`.venv\Scripts\python.exe tests\test_multipart.py` and `tests\test_store.py`
