from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock_youtube.server import MockYouTubeServer  # noqa: E402
from mock_youtube.sheets import MockSheetsServer  # noqa: E402


@pytest.fixture()
def mock_youtube():
    with MockYouTubeServer() as srv:
        yield srv


@pytest.fixture()
def mock_sheets():
    with MockSheetsServer(headers_row=["Report Date", "Schedule ID", "Sync Key", "Title"]) as srv:
        yield srv


@pytest.fixture()
def sample_file(tmp_path: Path) -> Path:
    """A deterministic 1 MiB file. 1 MiB = 4 x 256 KiB, so it is chunk-aligned."""
    p = tmp_path / "short.mp4"
    p.write_bytes(bytes((i * 31 + 7) % 256 for i in range(1024 * 1024)))
    return p
