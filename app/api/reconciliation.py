from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.models.enums import DuplicateStatus, ReviewStatus
from app.api.quickbooks import _qbo_client_from_db
from app.services.pnl_service import compute_pnl
from app.services.qbo_client import QboApiError, QboAuthError, parse_pnl_report_to_account_totals
from app.services.reconciliation_service import reconcile_period

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])

PERIODS = {
    "2026-04": (date(2026, 4, 1), date(2026, 4, 30)),
    "2026-05": (date(2026, 5, 1), date(2026, 5, 31)),
    "2026-06": (date(2026, 6, 1), date(2026, 6, 30)),
    "total": (date(2026, 4, 1), date(2026, 6, 30)),
}


@router.post("/run")
async def run_reconciliation(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Pulls the QBO cash-basis P&L for each period and compares it,
    account by account and on net profit, against the application's own
    P&L computed from approved canonical transactions. This is the final
    proof step -- a successful sync alone does not mean the books agree.
    """
    client = await _qbo_client_from_db(db)
    results = {}
    any_full_period_missing_qbo = False

    for label, (start, end) in PERIODS.items():
        cursor = db.transactions.find({
            "duplicate_status": DuplicateStatus.CANONICAL,
            "review_status": ReviewStatus.APPROVED,
            "transaction_date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
        })
        txns = [doc async for doc in cursor]
        app_pnl = compute_pnl(txns, label)

        try:
            report = await client.get_profit_and_loss(start, end)
            qbo_totals = parse_pnl_report_to_account_totals(report)
        except (QboApiError, QboAuthError) as exc:
            any_full_period_missing_qbo = True
            results[label] = {"error": str(exc), "app_pnl": asdict(app_pnl)}
            continue

        run = reconcile_period(
            period_start=start, period_end=end, period_label=label,
            app_pnl=app_pnl, qbo_account_totals=qbo_totals,
        )
        await db.reconciliation_runs.insert_one(run.model_dump(mode="json"))
        results[label] = run.model_dump(mode="json")

    if any_full_period_missing_qbo:
        raise HTTPException(502, detail={"message": "Could not reach QuickBooks for one or more periods", "results": results})

    return results


@router.get("/latest")
async def latest_reconciliation(db: AsyncIOMotorDatabase = Depends(get_db)):
    cursor = db.reconciliation_runs.find().sort("run_at", -1).limit(4)
    return [doc async for doc in cursor]
