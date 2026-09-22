---
name: personal-assistant
description: Handle personal messages through the verified V1 core.
version: 0.1.0
author: Li, Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [personal-assistant, feishu, accounting, reminders]
    related_skills: []
---

# Personal Assistant Skill

Pass Feishu DM text to the deterministic Personal Assistant Core. The Core owns routing, validation, idempotency, state machines, Feishu writes, Audit, and receipts.

## When to Use

- Personal accounting, correction, Task, Reminder, Capture, or Daily Review requests.
- Short personal chat that may need safe intent routing.

Don't use for monthly statement imports, reconciliation, or financial reports.

## Prerequisites

- Run only from a Hermes messaging session.
- `HERMES_SESSION_MESSAGE_ID` and `HERMES_SESSION_USER_ID` must be present.
- `$env:HERMES_HOME\bin\personal-assistant.cmd` must exist.

## How to Run

Use `terminal` once. Put the exact current user text in the here-string without summarizing it:

```powershell
$utf8 = [Text.UTF8Encoding]::new($false)
$OutputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$raw = @'
<exact current user text>
'@
@{text=$raw} | ConvertTo-Json -Compress | & "$env:HERMES_HOME\bin\personal-assistant.cmd"
```

## Procedure

1. Invoke the command once. Completion criterion: one JSON response is returned.
2. If `status=completed`, reply with `text` and do not add an independent success claim.
3. If `status=needs_clarification`, ask only the returned `text` question.
4. If `status=failed`, report returned `text`; do not call another bookkeeping path or retry a write.
5. If `status=chat`, reply with returned `text`.

## Pitfalls

- Never invent or replace the session message ID.
- Never call the old Buffett Base path as fallback.
- Never say a write succeeded from model inference or terminal exit alone.
- Do not expose command JSON, IDs, tokens, or local paths in the user reply.

## Verification

A valid response contains only `text`, `status`, and optional `receipt_id`. A completed mutation has a non-empty `receipt_id`.
