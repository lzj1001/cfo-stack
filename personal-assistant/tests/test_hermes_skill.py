from pathlib import Path
import re


ROOT = Path(__file__).parents[1]


def test_skill_is_thin_and_uses_session_aware_launcher():
    text = (ROOT / "hermes" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: personal-assistant\n")
    assert "personal-assistant.cmd" in text
    assert "HERMES_SESSION_MESSAGE_ID" in text
    assert "source-message-id auto" not in text
    assert "EXPENSE / INCOME" not in text
    assert text.count("personal-assistant.cmd") == 2


def test_launcher_uses_configured_repo_core_and_python():
    cmd = (ROOT / "hermes" / "personal-assistant.cmd").read_text(encoding="ascii")
    script = (ROOT / "hermes" / "personal-assistant.ps1").read_text(encoding="utf-8-sig")
    assert "%HERMES_HOME%\\bin\\personal-assistant.ps1" in cmd
    assert "PA_PROJECT_ROOT" in script
    assert "PA_PYTHON" in script
    assert "personal_assistant.hermes_entry" in script
    assert "Console]::InputEncoding" in script
    assert re.search(r"[A-Z]:\\", cmd + script) is None


def test_public_runtime_files_contain_no_machine_paths_or_live_resource_ids():
    files = [
        ROOT / ".env.example",
        ROOT / "hermes" / "scripts" / "pa_daily_review.py",
        ROOT / "hermes" / "scripts" / "pa_dispatch.py",
        ROOT / "src" / "personal_assistant" / "hermes_entry.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert re.search(r"[A-Z]:\\", text) is None
    for name in (
        "PA_BASE_TOKEN",
        "PA_DAILY_TABLE_ID",
        "PA_AUDIT_TABLE_ID",
        "PA_TASKS_TABLE_ID",
        "PA_REMINDERS_TABLE_ID",
        "PA_CAPTURES_TABLE_ID",
        "PA_REVIEWS_TABLE_ID",
    ):
        assert re.search(rf"^{name}=\s*$", text, re.MULTILINE)
