# AI Usage Note

## Tools Used

- Codex for implementation, debugging, UI refinement, and deployment support
- FastAPI for the backend
- MongoDB Atlas for application state and transaction storage
- Gemini as the optional classification fallback
- QuickBooks Online sandbox API for posting and reconciliation
- Render for deployment preparation
- GitHub for source control and repo delivery

## What Was AI-Assisted

- Initial project scaffolding and code organization
- Upload, review, P&L, QuickBooks sync, and reconciliation workflow wiring
- UI adjustments across desktop and mobile layouts
- QuickBooks reset and recovery handling after sandbox refreshes
- Submission documentation and deployment notes

## What Was Independently Validated

- Deterministic classification results against the challenge dataset
- Duplicate detection and exclusion from P&L and sync
- Monthly and three-month cash-basis P&L output
- QuickBooks sandbox posting, sync idempotency, and retry behavior
- Reconciliation against the QuickBooks cash-basis Profit and Loss report
- Reset flow for local and deployed environments

## Validation Strategy

- Ran the test suite and confirmed all automated tests passed
- Performed live QuickBooks sandbox syncs after reconnecting the company
- Verified reconciliation matched exactly for April, May, June, and total periods
- Repeated the full reset/reconnect cycle to confirm the app can start cleanly

## Notes

- The application uses AI as a helper, not as an unchecked source of truth.
- All accounting outputs were validated through tests and QuickBooks reconciliation.
- Sensitive secrets were kept out of source control and are intended for environment variables only.
