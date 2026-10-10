# PyInstaller build for DropTray: `.venv\Scripts\python.exe -m PyInstaller DropTray.spec --noconfirm`
# Produces dist\DropTray\DropTray.exe (a folder build: starts faster than a single-file exe).
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("web", "web"), ("droptray.ico", ".")]
binaries = []
hiddenimports = collect_submodules("winrt") + ["clr", "ui", "core", "updater"]
for pkg in ("cryptography",):
    hiddenimports += collect_submodules(pkg)
for pkg in ("webview", "clr_loader", "pythonnet", "windows_toasts", "pillow_heif", "zeroconf", "pystray"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(["droptray.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "tkinterdnd2", "playwright", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DropTray", icon="droptray.ico",
          console=False, upx=False, version=None)
coll = COLLECT(exe, a.binaries, a.datas, name="DropTray", upx=False)
