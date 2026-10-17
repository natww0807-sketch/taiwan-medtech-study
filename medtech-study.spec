# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

root = Path(SPECPATH)
app = root / "app"
datas = [
    (str(app / "static"), "static"),
    (str(app / "seed"), "seed"),
    (str(app / "images"), "images"),
    (str(app / "question_supplements.json"), "."),
    (str(root / "content"), "content"),
]

a = Analysis(
    [str(app / "launcher.py")],
    pathex=[str(app)],
    binaries=[],
    datas=datas,
    hiddenimports=["tkinter", "zoneinfo"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["openpyxl", "pdfplumber"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="醫技國考",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
bundle = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name="醫技國考")
if sys.platform == "darwin":
    app_bundle = BUNDLE(
        bundle,
        name="醫技國考.app",
        icon=None,
        bundle_identifier="tw.nat.medtech-study",
        info_plist={"CFBundleDisplayName": "醫技國考", "NSHighResolutionCapable": True},
    )
