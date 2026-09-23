import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pa_watchdog", ROOT / "hermes" / "scripts" / "pa_watchdog.py"
)
WATCHDOG = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(WATCHDOG)


def test_live_gateway_pid_does_not_restart():
    assert WATCHDOG.should_restart(
        {"gateway_state": "running", "pid": 123}, lambda pid: pid == 123
    ) is False


def test_stale_gateway_state_restarts():
    assert WATCHDOG.should_restart(
        {"gateway_state": "running", "pid": 123}, lambda pid: False
    ) is True


def test_missing_gateway_state_restarts():
    assert WATCHDOG.should_restart({}, lambda pid: True) is True


def test_runtime_paths_normalize_home_parent():
    hermes, python, dispatch = WATCHDOG.runtime_paths(
        Path("C:/Hermes/home/scripts/..")
    )

    assert str(hermes).endswith("C:\\Hermes\\home\\bin\\hermes.exe")
    assert str(python).endswith("C:\\Hermes\\app\\venv\\Scripts\\python.exe")
    assert str(dispatch).endswith("C:\\Hermes\\home\\scripts\\pa_dispatch.py")
