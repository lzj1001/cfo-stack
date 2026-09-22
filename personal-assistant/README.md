# Personal Assistant V1

V1 is deployed on the Windows Hermes host. Feishu DM text is routed directly by the Feishu adapter into this Core; Feishu Base is the business source of truth, and SQLite stores machine state only. The production model is configurable and currently uses SiliconFlow `Qwen/Qwen3.6-35B-A3B`.

## Current verification

- Real Feishu chat routing returns Core responses without entering the outer agent.
- Real Capture writes were deduplicated, read back, audited, and completed in SQLite.
- A real Reminder was created, sent by the minute dispatcher, acknowledged with `完成了`, and read back as Task `DONE` plus Reminder `COMPLETED`.
- The 23:30 Daily Review ran on schedule, updated the existing same-day record, delivered a structured Chinese review, and was verified against real Base totals.
- A create-success/readback-failure path is recoverable by re-reading the returned remote record ID; replay never sends a second create.
- Reminder Dispatcher runs every minute; Daily Review is scheduled for 23:30 Asia/Shanghai.
- The complete offline suite passes with fake LLM/Feishu transports and makes no production writes.

Hermes adapter upgrade/recovery instructions are in [`hermes/README.md`](hermes/README.md); the exact `0.21.3` hook is preserved as a credential-free patch.

## Included

- validated configuration and public domain models;
- SQLite operation idempotency and model-call metadata;
- Feishu mutation verification through immediate readback;
- structured-output SiliconFlow client with one repair attempt and bounded retry;
- receipt coordination that never repeats a successful business write when Audit is pending.

## Historical Phase 1 boundary

- Router prompts and production `handle_message`;
- accounting, Task, Reminder, Capture, or Daily Review business flows;
- Feishu table or field changes;
- production Base mutation tests.

## Phase 2 accounting behavior

Supported by the Core and covered with fake transports:

- single and multi-item expenses such as `午饭35，狗粮280`;
- income, refund linkage, transfers, credit-card repayments, and investments;
- stable `message_id + operation_index` idempotency;
- correction of one uniquely matched original record;
- verified write/readback plus remote Audit idempotency.

The program—not the model—converts money to integer fen, overrides credit-card and investment types, decides whether a write succeeded, and formats receipts.

`给小王500` is never written automatically. The response asks whether it is consumption, lending, repayment, or another purpose. Zero or multiple correction candidates also produce one clarification and no mutation.

TASK, REMINDER, CAPTURE, and REVIEW are recognized but remain side-effect free until their implementation phases.

## Phase 3 Task and Reminder behavior

- `明天晚上提醒我洗衣服` resolves to tomorrow 19:00 Asia/Shanghai and creates one Task plus one Reminder.
- Dayparts are deterministic: 08:30 / 12:00 / 15:00 / 19:00 / 20:00.
- Derived times in 00:00–08:00 shift to 08:00; an explicit clock time is honored.
- `完成了`、`晚点提醒`、`今天不做`、`别再催` bypass the LLM and use program-owned transitions.
- No reply never implies failure or completion. It permits at most one follow-up, then becomes EXHAUSTED.
- Sender failure does not consume the follow-up allowance.

Phase 3 still uses fake senders in tests. No live DM is sent until the Hermes integration phase.

## Phase 4 Capture behavior

- Capture types are idea/thought/insight/note/journal.
- `raw_text` is preserved exactly; AI title/tags are additional metadata only.
- Tags are limited to five.
- Capture never becomes a Task unless a future explicit conversion command is implemented.
- V1 uses no vector database.

## Phase 5 Daily Review behavior

- Program code computes expense fen, category totals, Task state counts, Capture topics, and Reminder completions.
- The summary model receives only the fact object and may return summary wording plus at most three priorities.
- Empty days use deterministic text and do not call the model.
- The existing same-day `每日复盘` record is updated; personal Journal fields are preserved.

### Phase 2 production schema delta

The approved add-only migration adds nine machine fields to `日常财务记录`, appends the `投资` option to `资金性质`, and creates an empty `Audit` table. It never deletes or renames existing fields and does not create a test transaction.

## Environment

Copy variable names from `.env.example` into Hermes secret storage. Never commit real values.

```dotenv
SILICONFLOW_API_KEY=
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
PA_ROUTER_MODEL=
PA_SUMMARY_MODEL=
PA_TIMEOUT_SECONDS=30
PA_MAX_RETRIES=2
PA_TIMEZONE=Asia/Shanghai
PA_DAILY_REVIEW_TIME=23:30
PA_MAX_FOLLOW_UPS=1
PA_PROJECT_ROOT=
PA_PYTHON=
```

## Tests

```powershell
Set-Location personal-assistant
uv run --extra dev pytest -q
```

Tests inject fake LLM and Feishu transports. They make no network requests and never write the production Base.

## Development model smoke

Development integration checks use Codex Luna for speed:

```powershell
& (Join-Path $env:HERMES_HOME 'bin\hermes.exe') chat `
  --provider openai-codex --model gpt-5.6-luna --reasoning low `
  --query '只回复 CODEX_LUNA_OK' --oneshot --quiet --run-budget 60
```

Expected final content: `CODEX_LUNA_OK`.

## Final SiliconFlow switch

After the business phases pass with Luna:

1. set Hermes provider back to the existing `custom` provider;
2. preserve `https://api.siliconflow.cn/v1` as the Base URL;
3. configure `PA_ROUTER_MODEL` and `PA_SUMMARY_MODEL`;
4. run the same structured fixtures against SiliconFlow;
5. run one non-mutating chat smoke;
6. verify Hermes Gateway remains connected before enabling normal messages.

The provider switch changes configuration only. Business state, idempotency keys, receipts, and Feishu records must remain unchanged.

## Production gate

Do not write the production Base until Phase 2 schema mapping and its idempotent migration are separately reviewed and approved.
