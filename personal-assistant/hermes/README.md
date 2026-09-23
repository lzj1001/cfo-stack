# Hermes integration

The deployed Feishu adapter routes text directly to the deterministic Personal Assistant Core when `platforms.feishu.personal_assistant_core_enabled` is true.

Hermes upgrades can replace the installed adapter. The exact Hermes `0.21.3` change is preserved in `patches/hermes-0.21.3-feishu-core-hook.patch`.

After an upgrade:

Set `HERMES_APP_ROOT` to the Hermes installation directory and `PA_PROJECT_ROOT` to this project's `personal-assistant` directory.

1. Check whether the hook is still installed:

   ```powershell
   $adapter = Join-Path $env:HERMES_APP_ROOT 'plugins\platforms\feishu\adapter.py'
   rg -n "personal_assistant_core_enabled|_run_personal_assistant_core" `
     $adapter
   ```

2. If the hook is absent, verify compatibility before applying it:

   ```powershell
   $patch = Join-Path $env:PA_PROJECT_ROOT 'hermes\patches\hermes-0.21.3-feishu-core-hook.patch'
   git -C $env:HERMES_APP_ROOT apply --check --unidiff-zero $patch
   ```

3. Only when `--check` succeeds, apply the patch with `--unidiff-zero`, restart the Gateway, then run one chat smoke and one idempotent replay. If the check fails on a newer Hermes version, audit the new adapter instead of forcing the old patch.

The patch contains no credentials, Base tokens, or user identifiers.

## Process hardening

`scripts/pa_watchdog.py` checks the real Gateway PID and runs the idempotent Reminder Dispatcher without an LLM. `scripts/pa_watchdog_loop.cmd` can be placed in the current user's Startup folder when Task Scheduler requires administrator permission. It restarts a stale Gateway and performs overdue Reminder recovery after restart.
