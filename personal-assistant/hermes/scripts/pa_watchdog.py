from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable


def should_restart(state: dict, process_exists: Callable[[int], bool]) -> bool:
    if state.get("gateway_state") != "running":
        return True
    try:
        pid = int(state.get("pid"))
    except (TypeError, ValueError):
        return True
    return not process_exists(pid)


def process_exists(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and f'"{pid}"' in result.stdout


def runtime_paths(home: Path) -> tuple[Path, Path, Path]:
    home = home.resolve()
    root = home.parent
    return (
        home / "bin" / "hermes.exe",
        root / "app" / "venv" / "Scripts" / "python.exe",
        home / "scripts" / "pa_dispatch.py",
    )


def load_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HERMES_HOME"] = str(home)
    path = home / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def run_once(
    home: Path,
    *,
    popen: Callable[..., object] = subprocess.Popen,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[bool, int]:
    home = home.resolve()
    env = load_env(home)
    state_path = home / "gateway_state.json"
    state = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    hermes, python, dispatch = runtime_paths(home)
    restarted = should_restart(state, process_exists)
    if restarted:
        popen(
            [str(hermes), "gateway", "run"],
            cwd=str(home.parent / "app"),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    result = run(
        [str(python), str(dispatch)],
        cwd=str(home.parent / "app"),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "dispatcher failed")
    return restarted, (1 if "sent=" in result.stdout else 0)


def main() -> int:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    interval = max(30, int(os.environ.get("PA_WATCHDOG_INTERVAL_SECONDS", "60")))
    while True:
        try:
            restarted, sent = run_once(home)
            print(
                json.dumps(
                    {"gateway_restarted": restarted, "sent": sent},
                    separators=(",", ":"),
                ),
                flush=True,
            )
        except Exception as error:
            print(json.dumps({"error": str(error)}, separators=(",", ":")), flush=True)
        if os.environ.get("PA_WATCHDOG_LOOP") != "1":
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
