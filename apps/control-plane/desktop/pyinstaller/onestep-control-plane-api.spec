# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]

entrypoint = ROOT / "backend" / "src" / "onestep_control_plane_api" / "desktop_entry.py"

a = Analysis(
    [str(entrypoint)],
    pathex=[str(ROOT / "backend" / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "alembic.ini"), "."),
        (str(ROOT / "backend" / "alembic"), "backend/alembic"),
    ],
    hiddenimports=["onestep_control_plane_api.db.models"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="onestep-control-plane-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
