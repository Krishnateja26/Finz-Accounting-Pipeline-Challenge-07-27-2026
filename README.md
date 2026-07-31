# Finz Accounting Data Pipeline — BrightFix Home Services LLC

A small accounting data platform: raw bank transactions in, classified and
reviewed cash-basis P&L out, synced to QuickBooks Online, and reconciled
against QuickBooks' own P&L to prove the numbers agree.

> **The most important property of this app:** a successful QuickBooks API
> sync is never treated as proof of correctness. The `/reconciliation`
> screen pulls QuickBooks' own P&L and diffs it, account by account and on
> net profit, against the app's internally computed P&L. Only when every
> line is `$0.00` different does a period show as **Reconciled**.

## Contents

- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Data model](#data-model)
- [Duplicate detection](#duplicate-detection)
- [Classification approach](#classification-approach)
- [Internal P&L](#internal-pl)
- [QuickBooks integration](#quickbooks-integration)
- [Reconciliation](#reconciliation)
- [Assumptions](#assumptions)
- [Known limitations](#known-limitations)
- [Validated results](#validated-results)
- [Deployment](#deployment)
- [Tests](#tests)

## Quick start

```bash
cp .env.example .env
# fill in QBO_CLIENT_ID / QBO_CLIENT_SECRET from developer.intuit.com,
# GEMINI_API_KEY, and a real MongoDB URI such as MongoDB Atlas

docker compose up --build
# App: http://localhost:8000
# Mongo: localhost:27017
```

Or run locally without Docker:

```bash
python -m venv .venv && source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -r requirements.txt
# start a local MongoDB (or point MONGO_URI at Atlas)
uvicorn app.main:app --reload
```

For a local UI smoke test without Docker or MongoDB, set `MONGO_URI=mongomock://localhost`
in `.env` before starting Uvicorn. This uses an in-memory database, so it is
only for demos/tests; use real MongoDB for the challenge walkthrough and QBO sync.

This submission was validated with MongoDB Atlas, Gemini API, and a live
QuickBooks Online sandbox.

Then walk the flow in order:

1. **`/`** — upload `data/sample_raw_transactions.json`-equivalent bank
   export (the original assignment workbook's "Raw Bank Transactions" tab,
   exported as CSV/XLSX), map columns, import.
2. **`/transactions`** — review, correct, and approve classifications.
3. **`/pnl`** — view monthly and consolidated P&L, drill into any line.
4. **`/reconciliation`** — connect the QBO sandbox, sync, pull QBO's P&L,
   and see the reconciliation table.

To seed the QBO sandbox's chart of accounts first:

```bash
python -m scripts.setup_qbo_chart_of_accounts
```

## Architecture

```
app/
├── main.py              FastAPI app, routes, Jinja2 pages
├── config.py             Settings from environment (.env)
├── database.py            Mongo connection + indexes (the real dedup control)
├── api/                    Thin HTTP layer -- validates input, calls services
│   ├── uploads.py            file upload + column-mapping preview + import
│   ├── transactions.py       review/correct/approve, chart-of-accounts
│   ├── pnl.py                  monthly/consolidated P&L + drill-down
│   ├── quickbooks.py            OAuth, idempotent sync
│   └── reconciliation.py        pull QBO P&L, diff vs. app P&L
├── models/                 Pydantic schemas for Mongo documents
├── services/                Pure business logic (no DB / no HTTP where possible)
│   ├── normalization.py       column mapping, parsing, fingerprinting
│   ├── deduplication.py         layered dedup decision logic
│   ├── classification.py         deterministic rules engine
│   ├── gemini_classifier.py       Gemini fallback, schema-validated
│   ├── ingestion.py                orchestrates the above against Mongo
│   ├── pnl_service.py               cash-basis P&L math
│   ├── qbo_client.py                  QBO OAuth + posting + reports
│   └── reconciliation_service.py       app P&L vs. QBO P&L diffing
├── templates/                Server-rendered Jinja2 + vanilla JS/fetch + Bootstrap
└── static/
```

**Design principle:** `normalization.py`, `deduplication.py`,
`classification.py`, `pnl_service.py`, and `reconciliation_service.py` are
pure functions with no MongoDB or HTTP dependency. This is what let the
accounting logic be validated directly against the assignment's known-correct
P&L figures without standing up a database (see
[Validated results](#validated-results)), and it's what tests/ exercises.
`ingestion.py`, the `api/` layer, and `qbo_client.py` are the "glue" that
wires the pure logic to Mongo and to QuickBooks.

## Data model

Two MongoDB collections carry the core invariant of this app: **raw data is
never edited, and nothing silently disappears.**

**`raw_transactions`** — exactly what was uploaded, one document per row,
keyed by `(import_batch_id, source_row_number)`. Never updated except to
stamp `processing_status` (`pending` / `processed` / `error`) and a link to
the resulting normalized transaction.

**`transactions`** — the normalized, classified, review-tracked record.
Money is stored as **integer cents**, never floats. Key fields:

| Field | Purpose |
|---|---|
| `duplicate_status` | `canonical` / `exact_duplicate` / `possible_duplicate` / `not_duplicate` |
| `transaction_type` | `revenue` / `cogs` / `operating_expense` / `refund` / `transfer` / `owner_contribution` / `owner_distribution` / `fixed_asset_purchase` |
| `classification_source` | `deterministic_rule` / `learned_rule` / `gemini` / `manual` |
| `classification_history` | append-only log of every classification this record has had, with source and confidence |
| `review_status` | `needs_review` / `approved` / `rejected` / `validation_error` |
| `qbo.*` | sync status, QBO entity/id, request id, attempt count, last error |

Supporting collections: `import_batches`, `classification_rules` (rules
learned from reviewer corrections), `qbo_sync_log` (append-only, keyed by
`qbo_request_id` for idempotency), `reconciliation_runs`.

## Duplicate detection

Layered, per `deduplication.py`:

1. **Primary key**: `(bank_account, bank_transaction_id)` when a bank
   transaction ID is present. Enforced by a **unique partial index** in
   Mongo (`database.py`) — this is the actual guarantee, not just an
   optimization. This is what catches the assignment's 5 planted duplicates.
2. **Fallback fingerprint**: `sha256(bank_account | date | amount |
   normalized_description)` when there's no transaction ID.
3. **Fuzzy candidates are never auto-merged.** Two legitimate ADP payroll
   runs a month apart can share amount/description patterns; only an exact
   key or fingerprint match is treated as a duplicate. (A `possible_duplicate`
   status exists in the enum for a future fuzzy-matching pass that flags,
   rather than merges, near-matches — not wired into the deterministic
   pipeline in this submission, to avoid false positives.)

Duplicates are **kept** in `transactions` (status `exact_duplicate`,
`duplicate_of_transaction_id` set) but excluded from the P&L and never
synced to QuickBooks. Re-uploading the same or an overlapping file is a
no-op for accounting purposes — verified in
`tests/test_duplicates.py::test_second_identical_upload_creates_zero_new_canonical_transactions`.

## Classification approach

**Rules first, Gemini second, human review last** (`ingestion.py::_classify`):

1. **Learned rules** (`classification_rules`, `created_from_correction=True`)
   — reused corrections take priority over everything, so once a reviewer
   fixes "Fleet Auto Care" once, it's never wrong again.
2. **Deterministic rules** (`classification.py`) — ~35 ordered
   vendor/keyword rules built directly from the dataset's actual
   descriptions (see the header comment in that file for the exact
   ordering rationale — vendor-specific expense rules like "Precision
   Install Svcs" are checked *before* the generic "INSTALL"/"PROJECT" →
   Installation Revenue keyword rule, so a subcontractor payment is never
   mistaken for installation revenue). High-confidence rule matches
   (≥0.95) are auto-approved; everything else waits for review.
3. **Gemini** (`gemini_classifier.py`) — only called when rules return
   nothing. Constrained to a strict JSON schema and the real chart of
   accounts; a response is **rejected** (falls through to manual review) if
   its account number isn't in the chart, its transaction type isn't in the
   allowed enum, confidence is missing/invalid, or the response doesn't
   parse. Gemini never auto-approves a transaction for sync — only a
   human (`POST /api/transactions/{id}/approve` or `/correct`) or a
   high-confidence rule can do that.
4. **Manual review** — the `/transactions` screen. Correcting a
   classification can optionally save it as a reusable rule
   ("Apply this classification to future transactions matching...").

## Internal P&L

`pnl_service.py` computes cash-basis P&L from **canonical + approved**
transactions only, bucketed by `transaction_date` (not `posted_date`, per
the assignment's recognition rule). Revenue accounts are `4000`–`4100`
(4100 Customer Refunds is contra-revenue), COGS are `5000`/`5010`, and every
other expense account in the chart of accounts is operating expense.
Transfers, owner contributions/distributions, and fixed-asset purchases are
excluded by `transaction_type`, not by account number, so the exclusion
logic doesn't silently break if new accounts are added. The `/pnl` page
lets you click any account line to drill into the transactions behind it
(`GET /api/pnl/{period}/accounts/{account}/transactions`).

## QuickBooks integration

**Entity mapping** (`qbo_client.py::ENTITY_FOR_TYPE`), per the assignment's
guidance that journal entries should be used sparingly:

| Transaction type | QBO entity |
|---|---|
| Customer receipt (revenue) | `Deposit` |
| Refund, expense, COGS payment, owner distribution, fixed-asset purchase | `Purchase` |
| Internal bank movement | `Transfer` |
| Owner contribution | `Deposit` (mapped to Owner's Equity) |

**Idempotency** (three layers, per `qbo_client.py` and `api/quickbooks.py`):

1. A **stable request id** based on `realm_id + bank_account +
   bank_transaction_id` (or fallback fingerprint when a bank transaction ID
   is missing), passed as QBO's `requestid` query param on every write.
   This stays stable even if the same source file is re-imported and the
   app generates new internal MongoDB IDs.
2. Before posting, the sync endpoint checks **our own `qbo_sync_log`** for a
   prior success with that request id and skips re-posting if found —
   avoids even sending a redundant request.
3. **Database state**: `transactions.qbo.qbo_sync_status` is only ever
   moved to `synced` once, and the sync query only selects
   `not_synced`/`failed` transactions, so re-running `POST
   /api/quickbooks/sync` after a full success is a no-op.

**OAuth**: standard authorization-code flow (`/api/quickbooks/connect` ->
Intuit consent screen -> `/api/quickbooks/callback`). The callback stores
the sandbox realm ID and access token in MongoDB for the challenge workflow.
In production, OAuth tokens should be encrypted or stored in a dedicated
secret store.

## Reconciliation

`reconciliation_service.py::reconcile_period` pulls
`GET /v3/company/{realm}/reports/ProfitAndLoss?accounting_method=Cash` for
April, May, June, and the full quarter, flattens QBO's nested report rows
(`qbo_client.py::parse_pnl_report_to_account_totals`), and diffs every
account plus net profit against the app's own P&L, with a **$0.01
tolerance**. A period is only marked `Reconciled` when every line matches —
this is enforced in code (`fully_reconciled = all(line.is_reconciled ...)`),
not just displayed as a suggestion.

## Assumptions

- **Cash-basis, `transaction_date`-based recognition**, per the Company
  Setup tab and the assignment's explicit instruction to use transaction
  date over posted date.
- **USD only** — the two supplied bank accounts (Operating Checking, Tax
  Reserve) and `USD` are hard-validated in `normalization.py`; any other
  value is flagged as a validation error rather than guessed at.
- **"PROJECT" indicates installation revenue**, same as "INSTALL" — found
  by validating the classification rules against the actual dataset
  descriptions (e.g. `CHECK DEPOSIT CEDAR GROVE MARKET PROJECT 5503`) and
  reconciling against the assignment's expected account-level P&L; without
  it, Repair Service Revenue and Installation Revenue don't split correctly
  even though total revenue still matches.
- A deterministic rule match at **≥0.95 confidence is "safe" and
  auto-approved**; everything else (including all Gemini output) requires
  human approval before it can sync to QuickBooks.
- The Milwaukee Commercial Tool Package purchase is capitalized to Tools &
  Equipment (`1500`), not expensed, per the assignment's explicit warning
  against expensing it.

## Known limitations

- **Fuzzy possible-duplicate detection is modeled but not implemented.**
  The `possible_duplicate` status exists in the schema for a future
  same-amount/near-date/different-description matching pass; this
  submission relies on the exact key + fingerprint layers only, which are
  sufficient for the supplied dataset and safer than guessing at fuzzy
  matches without a human in the loop.
- **No authentication/authorization** on the app itself — out of scope per
  the assignment's suggested fastest-viable-approach guidance, but a real
  deployment needs it before multiple reviewers touch the same data.
- **QuickBooks token storage is challenge/local-demo oriented.** OAuth
  tokens are stored in MongoDB so the evaluator can run the end-to-end flow;
  production should encrypt tokens or use a dedicated secret store.
- **Credentials are intentionally excluded from source control.** Configure
  MongoDB Atlas, Gemini, and QuickBooks through `.env` locally or hosting
  provider environment variables.

## Validated results

Run directly against `data/sample_raw_transactions.json` (extracted from the
assignment's own "Raw Bank Transactions" tab) through
`normalization.py` → `deduplication.py` → `classification.py` →
`pnl_service.py`:

| Check | Result |
|---|---|
| Raw rows | 200 |
| Unique canonical transactions | 195 |
| Exact duplicates | 5 (matches the assignment's 5 planted duplicate IDs exactly) |
| Unclassified canonical transactions | 0 |
| April / May / June / 3-month net profit | $21,990 / $28,285 / $17,905 / **$68,180** — exact match |
| All 17 account-level P&L lines | exact match |
| Combined ending cash balance | **$81,380** — exact match to the independent balance-sheet checksum |

## Live API validation

The final run was executed against MongoDB Atlas, Gemini API, and a live
QuickBooks Online sandbox:

| Check | Result |
|---|---|
| MongoDB Atlas connection | `atlas ping ok`, database `finz_accounting` |
| Gemini fallback test | Passed with `gemini-3.6-flash`, returned account `4000` and confidence `0.95` |
| QBO OAuth | Connected to sandbox realm `9341457600212703` |
| QBO chart of accounts setup | 21 accounts available, 0 failed |
| QBO sync | 183 posted, 12 internal transfers skipped, 0 failed |
| QBO persistent sync status | 195 synced canonical rows, 5 duplicate rows not synced |
| QBO reconciliation | April, May, June, and Apr-Jun total all reconciled with $0.00 differences |

See `deliverables/final_validation.md` for the detailed validation evidence.

## Deployment

The challenge app is deployed as a live FastAPI web service on Render and is
reachable here:

https://finz-accounting-pipeline-challenge-07-27.onrender.com/

### What we did to connect and deploy the app

1. Put the app source in GitHub and connected the repository to Render.
2. Set the application environment variables in Render, including:
   - `MONGO_URI`
   - `MONGO_DB_NAME`
   - `SECRET_KEY`
   - `ADMIN_RESET_TOKEN`
   - `GEMINI_API_KEY`
   - `QBO_CLIENT_ID`
   - `QBO_CLIENT_SECRET`
   - `QBO_REDIRECT_URI`
3. Pointed MongoDB Atlas at the app by adding the required IP allowlist
   entries during troubleshooting:
   - `74.220.50.0/24`
   - `74.220.58.0/24`
4. Updated the Intuit/QuickBooks redirect URI to the deployed callback:
   - `https://finz-accounting-pipeline-challenge-07-27.onrender.com/api/quickbooks/callback`
5. Fixed Render build issues caused by Python dependency compilation by:
   - pinning a compatible Python version
   - upgrading `pandas` and `pydantic` for the hosting environment
6. Fixed the Render startup command so the app runs with Uvicorn on the
   platform port instead of a missing `gunicorn` entry point.
7. Added a browser-based reset modal and safer API error handling so Mongo and
   reset issues show a clear message instead of a generic internal server
   error.

### Challenges we hit and how they were handled

- **Pandas / pydantic build failures on Render**: Render initially used a newer
  Python runtime that tried to compile binary dependencies from source. We
  resolved this by pinning a supported Python version and updating the package
  versions to ones that ship clean wheels for the target environment.
- **Missing `gunicorn` on deploy**: Render defaulted to a `gunicorn` startup
  command that was not present in the project. We switched the service to start
  the FastAPI app directly with `uvicorn app.main:app --host 0.0.0.0 --port
  $PORT`.
- **MongoDB Atlas connectivity**: The app showed a clear connection warning when
  Atlas was not reachable, which helped confirm that the issue was network
  access / allowlisting rather than app logic.
- **QuickBooks redirect mismatch**: Intuit rejected the OAuth flow until the
  deployed callback URL exactly matched the value registered in the Intuit app
  settings.

### Local and deployment notes

- Local development worked on `http://127.0.0.1:8001` during reset testing.
- The app uses MongoDB Atlas in deployed mode, so the running service needs a
  valid Atlas connection string and matching network access rules.
- `ADMIN_RESET_TOKEN` must stay private. It is used only for the app's reset
  modal.

For the full deployment checklist and environment variable reference, see
`DEPLOYMENT.md`.

## Tests

```bash
pytest
```

- `test_normalization.py` — valid parsing, missing date/amount/currency/bank
  account, missing transaction ID (non-fatal), accounting-style negatives,
  fingerprint stability, column-mapping auto-suggestion for a differently
  shaped bank export.
- `test_duplicates.py` — exact 195/5 split, correct duplicate IDs, zero new
  canonical transactions on re-upload.
- `test_classification.py` — zero unclassified transactions, install/project
  vs. repair revenue, subcontractor-named-"install" vendor isn't mistaken
  for installation revenue, fixed-asset vs. expense, owner activity, transfers.
- `test_pnl.py` — exact monthly/quarterly totals and all 17 account totals
  against the assignment's numbers; unapproved and duplicate transactions
  are excluded.
- `test_qbo_idempotency.py` — request-id stability/uniqueness, nested P&L
  report parsing.
- `test_reconciliation.py` — matching/mismatched/one-cent-tolerance cases.

## Validation Summary

The project was validated against the assignment brief and the actual
200-row dataset extracted from the uploaded workbook. The implementation was
checked end to end through ingestion, classification, duplicate detection,
P&L calculation, QuickBooks sync, and reconciliation.

- The classification rules were run against the **actual 200-row dataset**
  and refined until every monthly and account-level P&L figure matched the
  assignment's stated numbers exactly, including the independent
  balance-sheet checksum. One real bug was found and fixed during this
  process: the "PROJECT" keyword was initially missing from the
  installation-revenue rule, which caused the total revenue check to pass
  while the account-level split failed.
- The full pytest suite was run locally after dependencies were installed,
  and all 30 tests passed.
- MongoDB Atlas was connected and pinged successfully; the final database
  contained 200 raw rows, 195 approved canonical transactions, 5 exact
  duplicates, 183 QBO posting log entries, and reconciled QBO reports.
- Gemini was tested live with `gemini-3.6-flash` and returned a validated
  JSON classification.
- QuickBooks OAuth, chart-of-accounts setup, transaction sync, and P&L
  reconciliation were exercised against a live QuickBooks sandbox. April,
  May, June, and the Apr-Jun total all reconciled with $0.00 differences.
