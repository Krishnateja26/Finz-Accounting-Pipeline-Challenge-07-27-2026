# Submission Summary

## Included

- Source-code repository
- Working FastAPI application
- README with architecture, data model, classification, QuickBooks integration, duplicate prevention, assumptions, and known limitations
- Internal monthly and three-month cash-basis P&L statements
- QuickBooks sandbox P&L reconciliation output
- Deployment guide
- Protected admin reset flow for deployed test runs
- AI usage note

## Completed Application Flow

1. Upload raw bank data
2. Map columns and normalize transactions
3. Detect duplicates and preserve raw source rows
4. Classify transactions and allow review/correction
5. Build monthly and total cash-basis P&L
6. Connect to QuickBooks Online sandbox
7. Create the chart of accounts
8. Sync approved canonical transactions
9. Pull QuickBooks P&L
10. Reconcile application totals to QuickBooks

## Still Not Included

- Screen recording

## Deployment Notes

- The app is prepared for Render deployment.
- `SECRET_KEY`, `ADMIN_RESET_TOKEN`, and the QuickBooks callback URL are environment-based values.
- The deployed reset button is protected by `ADMIN_RESET_TOKEN`.

## Verification

- Automated test suite passed locally.
- Live QuickBooks sandbox sync and reconciliation were completed successfully.
- Reset flow was exercised multiple times to confirm a clean restart path.
