# BrightFix Home Services LLC - Internal Cash-Basis P&L
Generated from `data/sample_raw_transactions.json` via normalization -> deduplication -> classification -> pnl_service.
All 195 canonical transactions classified deterministically and approved; 5 duplicates excluded.

## Summary

| P&L section | April 2026 | May 2026 | June 2026 | Apr-Jun total |
|---|---:|---:|---:|---:|
| Revenue after refunds | $98,175 | $106,575 | $95,525 | $300,275 |
| Cost of Goods Sold | $31,325 | $32,050 | $30,475 | $93,850 |
| Gross profit | $66,850 | $74,525 | $65,050 | $206,425 |
| Operating expenses | $44,860 | $46,240 | $47,145 | $138,245 |
| **Net profit** | **$21,990** | **$28,285** | **$17,905** | **$68,180** |

## Account-level (3-month total)

| Account | Total |
|---|---:|
| Repair Service Revenue | $131,450 |
| Installation Revenue | $162,900 |
| Maintenance Plan Revenue | $10,950 |
| Customer Refunds | ($5,025) |
| Materials & Supplies | ($51,050) |
| Subcontractor Costs | ($42,800) |
| Payroll Expense | ($80,250) |
| Rent Expense | ($24,600) |
| Vehicle & Fuel | ($4,335) |
| Software & Subscriptions | ($4,335) |
| Marketing & Advertising | ($8,850) |
| Insurance Expense | ($3,675) |
| Utilities | ($3,645) |
| Professional Fees | ($4,950) |
| Bank Fees | ($105) |
| Office & General | ($930) |
| Repairs & Maintenance | ($2,570) |

## Independent balance-sheet checksum

| Bank account | Ending balance |
|---|---:|
| Operating Checking | $39,380 |
| Tax Reserve | $42,000 |
| **Combined cash** | **$81,380** |

Checksum: $68,180 net profit + $25,000 owner contribution - $5,000 owner distribution - $6,800 equipment purchase = **$81,380**.

All figures above exactly match the pipeline output validated in `tests/test_pnl.py` and `tests/test_duplicates.py`.

## QuickBooks P&L and reconciliation output

Validated against a live QuickBooks Online sandbox after syncing approved canonical transactions.

QuickBooks setup and sync:

```text
Chart of accounts: 21 available, 0 failed
Synced posted transactions: 183
Internal transfers skipped: 12
Failed syncs: 0
Exact duplicates not synced: 5
```

Reconciliation result:

```text
2026-04: Reconciled, all differences $0.00
2026-05: Reconciled, all differences $0.00
2026-06: Reconciled, all differences $0.00
Apr-Jun total: Reconciled, all differences $0.00
```

See `deliverables/final_validation.md` for the full validation summary.
