# Final Validation Evidence

## Runtime Stack

- Application: Python + FastAPI
- Database: MongoDB Atlas, database `finz_accounting`
- AI fallback: Gemini API, `gemini-3.6-flash`
- Accounting integration: QuickBooks Online sandbox API

## Gemini Validation

The Gemini fallback endpoint was tested successfully:

```json
{
  "transaction_type": "revenue",
  "counterparty": "TEST CUSTOMER",
  "account_number": "4000",
  "confidence": 0.95,
  "explanation": "Inbound ACH credit payment from a customer for a repair job is classified as Repair Service Revenue.",
  "source": "gemini"
}
```

For the supplied dataset, deterministic rules classify every canonical transaction, so Gemini is present as a fallback for future unrecognized rows.

## MongoDB Atlas State

After a clean import and approval flow:

```text
import_batches: 1
raw_transactions: 200
transactions: 200
qbo_sync_log: 183
reconciliation_runs: 8
app_settings: 1
```

Transaction state:

```text
195 approved canonical
5 needs_review exact_duplicate
```

Transaction type counts:

```text
revenue: 81
operating_expense: 57
cogs: 39
transfer: 12
refund: 3
fixed_asset_purchase: 1
owner_distribution: 1
owner_contribution: 1
unclassified exact duplicates: 5
```

## QuickBooks Sync

QuickBooks sandbox connection succeeded for realm `9341457600212703`.

Chart of accounts setup:

```text
Created: 0
Already existed: 21
Failed: 0
```

Sync result:

```text
Synced: 183
Already synced skipped: 0
Internal transfers skipped: 12
Failed: 0
```

Persistent sync state:

```text
synced: 195
not_synced: 5
```

The 5 not-synced rows are exact duplicate source rows and are intentionally not posted.

## QuickBooks Reconciliation

The application P&L reconciled to QuickBooks with zero differences:

```text
2026-04: Reconciled, all account differences 0.00
2026-05: Reconciled, all account differences 0.00
2026-06: Reconciled, all account differences 0.00
Apr-Jun total: Reconciled, all account differences 0.00
```

Three-month total reconciled P&L:

```text
Revenue after refunds: 300,275
COGS: -93,850
Gross profit: 206,425
Operating expenses: -138,245
Net profit: 68,180
```
