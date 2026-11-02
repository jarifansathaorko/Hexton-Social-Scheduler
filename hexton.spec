# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

REPO_ROOT = Path(SPECPATH).resolve()
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

datas = [
    (str(FRONTEND_DIST), "frontend/dist"),
    (str(BACKEND_DIR / "alembic.ini"), "."),
    (str(BACKEND_DIR / "migrations"), "migrations"),
]

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "alembic",
    "alembic.context",
    "alembic.runtime.migration",
    "alembic.operations",
    "sqlalchemy.sql.default_comparator",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    "sse_starlette",
    "tzdata",
    "zoneinfo",
    "googleapiclient",
    "googleapiclient.discovery",
    "googleapiclient.http",
    "googleapiclient.errors",
    "google.oauth2",
    "google.oauth2.credentials",
    "google.auth",
    "google.auth.transport",
    "google.auth.transport.requests",
    *collect_submodules("hexton"),
]

a = Analysis(
    [str(BACKEND_DIR / "hexton" / "__main__.py")],
    pathex=[str(BACKEND_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Hexton",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
