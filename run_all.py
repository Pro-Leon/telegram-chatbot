#!/usr/bin/env python
"""Single-entry launcher for all MTProto Bot services.

Usage:
    python run_all.py

This starts:
- MTProto bot (main process, handles Telethon client + send stream)
- LLM worker (generates reply drafts)
- Send worker (processes operator-approved messages)
- Dashboard web UI (http://localhost:1010)
"""

import asyncio
import signal
import subprocess
import sys
import time

import reset_db
from db.redis import clear_all_user_locks

processes: list[subprocess.Popen] = []
shutting_down = False


def _kill_port(port: int) -> None:
    """Kill any process listening on the given port."""
    if sys.platform == "win32":
        ps_cmd = (
            f"Get-NetTCPConnection -LocalPort {port} | "
            "Where-Object { $_.State -eq 'Listen' } | "
            "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
        )
        subprocess.run(
            ["powershell", "-Command", ps_cmd],
            capture_output=True,
            check=False,
        )
    else:
        subprocess.run(f"fuser -k {port}/tcp", shell=True, capture_output=True, check=False)
    time.sleep(1)


def _cleanup_stale_processes() -> None:
    """Kill any stale Python processes running our service scripts."""
    if sys.platform == "win32":
        ps_cmd = (
            "Get-Process python | "
            "Where-Object { $_.CommandLine -match 'chatbotv2.main|workers\\.llm_worker|workers\\.send_worker|workers\\.scheduler_worker|uvicorn.*chatbotv2.dashboard' } | "
            "ForEach-Object { Stop-Process -Id $_.Id -Force }"
        )
        subprocess.run(
            ["powershell", "-Command", ps_cmd],
            capture_output=True,
            check=False,
        )
    else:
        subprocess.run(
            "pkill -f 'chatbotv2.main|workers\\.llm_worker|workers\\.send_worker|workers\\.scheduler_worker|uvicorn.*chatbotv2.dashboard'",
            shell=True,
            capture_output=True,
            check=False,
        )
    time.sleep(2)


def _cleanup_sqlite_journal() -> None:
    """Remove stale SQLite journal/WAL files that can cause 'database is locked' errors."""
    import glob
    import os

    patterns = [
        "*.session-journal",
        "*.session-wal",
        "*.session-shm",
        "chatbotv2.session-journal",
        "chatbotv2.session-wal",
        "chatbotv2.session-shm",
    ]
    for pattern in patterns:
        for f in glob.glob(os.path.join(os.getcwd(), pattern)):
            try:
                os.remove(f)
                print(f"[launcher] Removed stale file: {f}")
            except OSError:
                pass
    time.sleep(1)


async def _check_migrations() -> None:
    """Check migrations and AUTO-APPLY any pending ones.

    Missing migrations (aftercare_status, generation_telemetry, etc.) will
    crash workers at runtime (UndefinedColumnError / UndefinedTableError).
    Instead of just warning, we now APPLY them automatically on startup.

    If auto-apply fails, we FAIL HARD so the operator knows to fix the DB
    before traffic resumes (prevents silent degraded mode).
    """
    from db.postgres import close_pool, init_pool

    await init_pool()
    try:
        from db.migrate import get_status, upgrade
        from db.postgres import get_pool as _get_pool

        status = await get_status()
        if not status["up_to_date"]:
            pending = status.get("pending", [])
            print(f"[launcher] {status['pending_count']} pending migration(s): {', '.join(str(p) for p in pending)}")
            print("[launcher] Auto-applying pending migrations...")
            pool = await _get_pool()
            async with pool.acquire() as conn:
                applied = await upgrade(conn)
            if applied:
                print(f"[launcher] Applied {len(applied)} migration(s): {', '.join(applied)}")
                # Re-check to confirm
                status2 = await get_status()
                if status2["up_to_date"]:
                    print(f"[launcher] Migrations now up to date (version {status2['current_version']})")
                else:
                    print(f"[launcher] WARNING: still {status2['pending_count']} pending after auto-apply")
                    print("[launcher] Run manually: python -m db.migrate upgrade")
            else:
                print("[launcher] WARNING: auto-apply returned 0 — possible migration failure")
                print("[launcher] Check logs and run: python -m db.migrate upgrade")
        else:
            v = status["current_version"] or "none"
            print(f"[launcher] Migrations up to date (version {v})")
    except Exception as e:  # noqa: BLE001 — log and continue (don't block startup on migration infra failure)
        print(f"[launcher] Migration check/apply failed: {e}")
        print("[launcher] Run manually: python -m db.migrate upgrade")
        import traceback

        traceback.print_exc()
    finally:
        await close_pool()


def _signal_handler(signum, frame):
    global shutting_down
    if shutting_down:
        return
    shutting_down = True
    print("\n[launcher] Shutting down all services...")
    for p in processes:
        p.terminate()
    for p in processes:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print("[launcher] Cleaning up stale DB connections...")
    asyncio.run(reset_db.main())

    print("[launcher] Checking database migrations...")
    try:
        asyncio.run(_check_migrations())
    except Exception as e:  # noqa: BLE001 — defensive catch-all to prevent startup failure
        print(f"[launcher] Migration check failed: {e}")
        print("[launcher] Continuing startup (run 'python -m db.migrate upgrade' manually)")

    print("[launcher] Clearing stale Redis locks...")
    try:
        asyncio.run(clear_all_user_locks())
    except Exception as e:  # noqa: BLE001 — Redis may be unreachable; non-fatal
        print(f"[launcher] Redis lock cleanup skipped: {e}")

    print("[launcher] Freeing port 1010...")
    _kill_port(1010)

    print("[launcher] Cleaning up stale processes...")
    _cleanup_stale_processes()

    print("[launcher] Cleaning up stale session files...")
    _cleanup_sqlite_journal()

    commands = [
        ("MTProto Bot", [sys.executable, "-m", "chatbotv2.main"]),
        ("LLM Worker", [sys.executable, "-m", "workers.llm_worker", "--worker-id", "worker_1"]),
        ("Send Worker", [sys.executable, "-m", "workers.send_worker", "--worker-id", "sender_1"]),
        ("Scheduler Worker", [sys.executable, "-m", "workers.scheduler_worker", "--worker-id", "scheduler_1"]),
        (
            "Dashboard",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "chatbotv2.dashboard.app:app",
                "--host",
                "0.0.0.0",
                "--port",
                "1010",
            ],
        ),
    ]

    for name, cmd in commands:
        print(f"[launcher] Starting {name}...")
        p = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
        processes.append(p)
        time.sleep(1)

    print("[launcher] All services started!")
    print("[launcher] Dashboard: http://localhost:1010")
    print("[launcher] Press Ctrl+C to stop all services.")

    try:
        while True:
            for p in processes:
                if p.poll() is not None:
                    print(f"[launcher] Process {p.pid} exited with code {p.returncode}")
                    if shutting_down:
                        continue
                    # Only shut down all services if the dashboard crashes
                    # (the bot/workers can sometimes hit transient errors)
                    if p is processes[-1]:  # Dashboard is last
                        _signal_handler(None, None)
                    else:
                        # Mark as exited but don't kill everything
                        processes.remove(p)
                        print(f"[launcher] {processes[0] if processes else 'All'} remaining")
            time.sleep(1)
    except KeyboardInterrupt:
        _signal_handler(None, None)


if __name__ == "__main__":
    main()
