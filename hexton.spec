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
    "hexton",
    "hexton.api.app",
    "hexton.api.routers.compliance",
    "hexton.api.routers.dashboard",
    "hexton.api.routers.events",
    "hexton.api.routers.jobs",
    "hexton.api.routers.media",
    "hexton.api.routers.mentions",
    "hexton.api.routers.reporting",
    "hexton.api.routers.connections",
    "hexton.cli",
    "hexton.worker",
    "hexton.worker.main",
    "hexton.worker.runner",
    "hexton.worker.sweep",
    "hexton.worker.heartbeat",
    "hexton.worker.report_sync",
    "hexton.worker.lease",
    "hexton.media.picker",
    "hexton.connectors.youtube",
    "hexton.connectors.sheets_live",
    "hexton.connectors.fake",
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
