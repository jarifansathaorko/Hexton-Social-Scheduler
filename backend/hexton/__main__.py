"""``python -m hexton`` — the single entry point for every mode."""

from __future__ import annotations

from hexton.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
