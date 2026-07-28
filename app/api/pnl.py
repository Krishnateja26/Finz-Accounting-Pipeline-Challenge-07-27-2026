from dataclasses import asdict

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.models.enums import DuplicateStatus, ReviewStatus
from app.services.pnl_service import compute_monthly_and_total

router = APIRouter(prefix="/api/pnl", tags=["pnl"])


async def _eligible_transactions(db: AsyncIOMotorDatabase) -> list[dict]:
    cursor = db.transactions.find({
        "duplicate_status": DuplicateStatus.CANONICAL,
        "review_status": ReviewStatus.APPROVED,
    })
    return [doc async for doc in cursor]


@router.get("")
async def get_pnl(db: AsyncIOMotorDatabase = Depends(get_db)):
    """Returns April, May, June, and the combined 3-month P&L. Each account
    line includes the transaction ids behind it for drill-down."""
    txns = await _eligible_transactions(db)
    periods = compute_monthly_and_total(txns)
    return {label: asdict(period) for label, period in periods.items()}


@router.get("/{period}/accounts/{account_number}/transactions")
async def drill_down(period: str, account_number: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Returns the actual transactions behind one P&L line, e.g.
    /api/pnl/2026-04/accounts/5000/transactions -> the April Materials &
    Supplies transactions."""
    query = {
        "duplicate_status": DuplicateStatus.CANONICAL,
        "review_status": ReviewStatus.APPROVED,
        "qbo_account_number": account_number,
    }
    if period != "total":
        query["transaction_date"] = {"$regex": f"^{period}"}
    cursor = db.transactions.find(query).sort("transaction_date", 1)
    return [doc async for doc in cursor]
