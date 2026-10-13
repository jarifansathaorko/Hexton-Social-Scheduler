"""Command-line entry points: ``migrate``, ``worker``, ``serve``, ``dev``.

``serve`` is the normal way to run the application. It starts the API **and** spawns
the worker as a genuinely separate process, because the requirement is not "the worker
runs in a thread" but "closing the browser does not stop the worker" — and a thread
dies with its process. The child is terminated cleanly when the parent exits, and if
the parent is killed the child's leases simply expire and are reclaimed on the next
start, which is the recovery path the tests exercise.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path

from hexton import __version__
from hexton.bootstrap import AppContext, build_context
from hexton.config import Settings, get_settings
from hexton.connectors.registry import BuildProfile

BACKEND_ROOT = (
    Path(sys._MEIPASS)
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    else Path(__file__).resolve().parents[1]
)


def _context(*, packaged: bool | None = None, test_mode: bool = False) -> AppContext:
    settings = get_settings()
    if test_mode and not settings.test_mode:
        settings = settings.model_copy(update={"test_mode": True})
    profile = (
        _profile(settings)
        if packaged is None
        else (BuildProfile.PACKAGED if packaged else BuildProfile.DEVELOPMENT)
    )
    return build_context(settings, profile=profile)


def _in_source_checkout() -> bool:
    """True when this code is running from a source tree rather than an installation.

    ``pyproject.toml`` sits directly beside the package in a checkout and cannot sit
    beside it in a wheel install, where ``hexton`` is placed in ``site-packages``. That
    makes it a marker of *how the code arrived on disk*, which is the question the build
    profile answers — unlike an environment variable, which a misconfiguration can set,
    and unlike a marker file, which nothing had ever written.

    An editable install still counts as a checkout, and correctly so: the code being run
    is the developer's working tree.
    """
    if getattr(sys, "frozen", False):
        return False
    return (BACKEND_ROOT / "pyproject.toml").is_file()


def _profile(settings: Settings) -> BuildProfile:
    """How this process was launched.

    A packaged build is one where the application was installed rather than run from a
    checkout, and it is the profile that refuses the fake connector and the plaintext
    credential store. This used to be decided solely by a marker under ``data_dir`` that
    no code ever created, so ``BuildProfile.PACKAGED`` was unreachable and every guard
    keyed on it — the fake-connector refusal, the plaintext-store refusal, and
    ``attach-test-credential`` — was dead code. A default install therefore reported
    published figures for uploads that could only ever have been simulated.

    Now: an installer may still write the marker to force the packaged profile, but its
    absence no longer implies "development". Absence means "not installed from a
    checkout", which is what an installation is.
    """
    marker = settings.data_dir / "packaged"
    if marker.exists():
        return BuildProfile.PACKAGED
    return BuildProfile.DEVELOPMENT if _in_source_checkout() else BuildProfile.PACKAGED


# ----------------------------------------------------------------------
# migrate
# ----------------------------------------------------------------------


def cmd_migrate(args: argparse.Namespace) -> int:
    """Apply migrations to the configured database."""
    from alembic import command
    from alembic.config import Config

    settings = get_settings()
    settings.ensure_directories()
    ini_path = BACKEND_ROOT / "alembic.ini"
    migrations_dir = BACKEND_ROOT / "migrations"
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(migrations_dir))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{settings.database_path}")
    config.attributes["configure_logger"] = True
    revision = getattr(args, "revision", "head")
    if revision == "base":
        command.downgrade(config, "base")
    else:
        command.upgrade(config, revision)
    print(f"Database at {settings.database_path} is at revision {revision}.")
    return 0


def cmd_revision(args: argparse.Namespace) -> int:
    """Generate a new migration from the models."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["configure_logger"] = True
    command.revision(config, autogenerate=True, message=args.message, rev_id=args.rev_id)
    return 0


# ----------------------------------------------------------------------
# worker
# ----------------------------------------------------------------------


def cmd_worker(args: argparse.Namespace) -> int:
    from hexton.worker.main import run_worker

    is_test = getattr(args, "test_mode", False)
    ctx = _context(test_mode=is_test)

    # In production, fake connector is forbidden unless test mode is explicitly signaled
    if ctx.connector.provider == "fake" and not ctx.settings.test_mode:
        print(
            "error: worker cannot bind to the fake connector in production. "
            "Fake connectors are only permitted in test mode. "
            "To run with fake connectors, pass --test-mode or set HEXTON_TEST_MODE=1.",
            file=sys.stderr,
        )
        return 1

    return run_worker(ctx)


# ----------------------------------------------------------------------
# serve
# ----------------------------------------------------------------------


def _spawn_worker(ctx: AppContext) -> subprocess.Popen | None:
    """Start the worker as a child process.

    ``--no-start-worker`` exists so a worker can be run in its own terminal during
    development, and so tests can drive the runner directly without a second process
    competing for the same jobs.
    """
    env = dict(os.environ)
    env["HEXTON_DATA_DIR"] = str(ctx.settings.data_dir)
    env["HEXTON_CONNECTOR_KIND"] = ctx.settings.connector_kind
    if ctx.settings.test_mode:
        env["HEXTON_TEST_MODE"] = "1"
    creationflags = 0
    if os.name == "nt":  # pragma: no cover - Windows only
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "worker"]
        else:
            cmd = [sys.executable, "-m", "hexton", "worker"]
        if ctx.settings.test_mode:
            cmd.append("--test-mode")
        cwd = (
            str(Path(sys.executable).parent) if getattr(sys, "frozen", False) else str(BACKEND_ROOT)
        )
        return subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            creationflags=creationflags,
        )
    except OSError as exc:  # pragma: no cover - environment specific
        print(f"warning: could not start the worker process: {exc}", file=sys.stderr)
        return None


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from hexton.api.app import create_app

    is_test = getattr(args, "test_mode", False)
    ctx = _context(test_mode=is_test)

    # In production startup, binding to fake connectors is refused unless explicitly in test mode
    if ctx.connector.provider == "fake" and not ctx.settings.test_mode:
        print(
            "error: 'serve' cannot bind to the fake connector in production. "
            "Fake connectors are only permitted in test mode. "
            "To run with fake connectors in tests, pass --test-mode or set HEXTON_TEST_MODE=1.",
            file=sys.stderr,
        )
        return 1

    app = create_app(ctx)

    child: subprocess.Popen | None = None
    if not args.no_start_worker:
        child = _spawn_worker(ctx)
        if child is not None:
            print(f"Worker started as process {child.pid}.")

    def shutdown(*_: object) -> None:
        if child is not None and child.poll() is None:
            # Graceful first, so the worker finishes its current step and releases the
            # lease rather than leaving it to expire.
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - timing
                child.kill()

    if child is not None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(ValueError, OSError):  # pragma: no cover - non-main thread
                signal.signal(sig, lambda *_: shutdown())
        watcher = threading.Thread(target=lambda: child.wait(), daemon=True)
        watcher.start()

    if getattr(args, "open_browser", False):
        import time
        import webbrowser

        def _open() -> None:
            time.sleep(1.0)
            webbrowser.open(f"http://{ctx.settings.host}:{ctx.settings.port}")

        threading.Thread(target=_open, daemon=True).start()

    try:
        uvicorn.run(
            app,
            host=ctx.settings.host,
            port=ctx.settings.port,
            log_level="info",
            access_log=False,
        )
    finally:
        shutdown()
        ctx.close()
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    """API and worker in one process, for quick iteration.

    Not the production shape: the point of the separate worker process is that it
    outlives the browser and can be reasoned about independently. This command exists
    so a developer can iterate without a second terminal, and it says so on startup.
    """
    import uvicorn

    from hexton.api.app import create_app
    from hexton.worker.main import Worker

    is_test = getattr(args, "test_mode", False)
    ctx = _context(test_mode=is_test)
    app = create_app(ctx)
    worker = Worker(ctx, owner="dev-worker")
    worker.install_signal_handlers()
    thread = threading.Thread(target=worker.run_forever, kwargs={"poll_seconds": 0.5}, daemon=True)
    thread.start()
    print("development mode: the worker runs in a thread; use `serve` for the real shape.")
    try:
        uvicorn.run(
            app,
            host=ctx.settings.host,
            port=ctx.settings.port,
            log_level="warning",
            access_log=False,
        )
    finally:
        worker.request_stop()
        ctx.close()
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    ctx = _context()
    print(f"hexton           {__version__}")
    print(f"data directory   {ctx.settings.data_dir}")
    print(f"database         {ctx.settings.database_path}")
    print(f"connector        {ctx.connector.provider}")
    print(f"credential store {ctx.credentials.name}")
    print(f"build profile    {ctx.profile.value}")
    print(f"listen on        http://{ctx.settings.host}:{ctx.settings.port}")
    ctx.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # When launched with no arguments (e.g. double-clicking Hexton.exe), run migrations,
    # start the server and worker, and automatically pop the web browser.
    if not argv:
        print(f"Hexton Scheduler v{__version__} starting...")
        migrate_args = argparse.Namespace(revision="head")
        cmd_migrate(migrate_args)
        serve_args = argparse.Namespace(
            no_start_worker=False,
            test_mode=False,
            open_browser=True,
        )
        return cmd_serve(serve_args)

    parser = argparse.ArgumentParser(prog="hexton", description="Hexton local scheduler")
    parser.add_argument("--version", action="version", version=f"hexton {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    migrate = sub.add_parser("migrate", help="apply database migrations")
    migrate.add_argument("--revision", default="head")
    migrate.set_defaults(func=cmd_migrate)

    revision = sub.add_parser("revision", help="generate a migration from the models")
    revision.add_argument("--message", required=True)
    revision.add_argument("--rev-id", default=None)
    revision.set_defaults(func=cmd_revision)

    worker = sub.add_parser("worker", help="run the background worker")
    worker.add_argument(
        "--test-mode", action="store_true", help="enable test-mode execution with fake connectors"
    )
    worker.set_defaults(func=cmd_worker)

    serve = sub.add_parser("serve", help="run the API and the worker")
    serve.add_argument("--no-start-worker", action="store_true")
    serve.add_argument(
        "--test-mode", action="store_true", help="enable test-mode execution with fake connectors"
    )
    serve.add_argument(
        "--open-browser", action="store_true", help="open default browser to the web UI on launch"
    )
    serve.set_defaults(func=cmd_serve)

    dev = sub.add_parser("dev", help="run the API and worker in one process")
    dev.add_argument(
        "--test-mode", action="store_true", help="enable test-mode execution with fake connectors"
    )
    dev.set_defaults(func=cmd_dev)

    info = sub.add_parser("info", help="show the resolved configuration")
    info.set_defaults(func=cmd_info)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
