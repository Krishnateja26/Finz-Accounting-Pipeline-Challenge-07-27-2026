"""
Dashboard API.

Purely additive and read-only: every number here is derived by querying
existing collections and, where relevant, reusing the exact same
`pnl_service.compute_pnl` function the /pnl page uses -- so the dashboard can
never disagree with the P&L page because it isn't computing anything new,
just summarizing what's already there.
"""
from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_db
from app.models.enums import DuplicateStatus, ReviewStatus
from app.services.pnl_service import compute_pnl

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(db: AsyncIOMotorDatabase = Depends(get_db)):
    raw_count = await db.raw_transactions.count_documents({})
    canonical_count = await db.transactions.count_documents({"duplicate_status": DuplicateStatus.CANONICAL})
    duplicate_count = await db.transactions.count_documents({"duplicate_status": DuplicateStatus.EXACT_DUPLICATE})
    needs_review_count = await db.transactions.count_documents({"review_status": ReviewStatus.NEEDS_REVIEW})
    approved_count = await db.transactions.count_documents({"review_status": ReviewStatus.APPROVED})
    error_count = await db.raw_transactions.count_documents({"processing_status": "error"})

    sync_pipeline = [
        {"$match": {"duplicate_status": DuplicateStatus.CANONICAL}},
        {"$group": {"_id": "$qbo.qbo_sync_status", "count": {"$sum": 1}}},
    ]
    sync_counts = {doc["_id"]: doc["count"] async for doc in db.transactions.aggregate(sync_pipeline)}
    latest_sync_cursor = db.qbo_sync_log.find({}, {"_id": 0}).sort("synced_at", -1).limit(1)
    latest_sync = await latest_sync_cursor.to_list(length=1)
    latest_sync = latest_sync[0] if latest_sync else None

    cursor = db.transactions.find({
        "duplicate_status": DuplicateStatus.CANONICAL,
        "review_status": ReviewStatus.APPROVED,
    })
    approved_txns = [doc async for doc in cursor]
    total_pnl = compute_pnl(approved_txns, "total")

    categories = [
        {
            "account_number": line.account_number,
            "account_name": line.account_name,
            "amount_cents": line.amount_cents,
            "transaction_count": len(line.transaction_ids),
        }
        for line in total_pnl.account_lines
    ]
    max_abs = max((abs(c["amount_cents"]) for c in categories), default=1)
    for c in categories:
        c["bar_pct"] = round(abs(c["amount_cents"]) / max_abs * 100, 1) if max_abs else 0

    recon_run_count = await db.reconciliation_runs.count_documents({})
    latest_recon_cursor = db.reconciliation_runs.find({}, {"_id": 0}).sort("run_at", -1).limit(1)
    latest_recon = await latest_recon_cursor.to_list(length=1)
    latest_recon = latest_recon[0] if latest_recon else None

    recent_batches_cursor = db.import_batches.find({}, {"_id": 0}).sort("created_at", -1).limit(5)
    recent_batches = [doc async for doc in recent_batches_cursor]

    return {
        "counts": {
            "raw_rows": raw_count,
            "canonical_transactions": canonical_count,
            "duplicates": duplicate_count,
            "needs_review": needs_review_count,
            "approved": approved_count,
            "processing_errors": error_count,
            "synced": sync_counts.get("synced", 0),
            "sync_failed": sync_counts.get("failed", 0),
            "not_synced": sync_counts.get("not_synced", 0),
            "duplicates_excluded_from_sync": duplicate_count,
        },
        "pnl_summary": {
            "revenue_cents": total_pnl.revenue_cents,
            "cogs_cents": total_pnl.cogs_cents,
            "gross_profit_cents": total_pnl.gross_profit_cents,
            "opex_cents": total_pnl.opex_cents,
            "net_profit_cents": total_pnl.net_profit_cents,
        },
        "categories": categories,
        "reconciliation": {
            "total_runs": recon_run_count,
            "latest": latest_recon,
        },
        "quickbooks": {
            "latest_sync": latest_sync,
        },
        "recent_batches": recent_batches,
    }


@router.get("/runs")
async def runs_history(db: AsyncIOMotorDatabase = Depends(get_db)):
    """A unified, read-only timeline of every reconciliation run and every
    QuickBooks sync attempt, most recent first -- purely a display surface
    over the qbo_sync_log / reconciliation_runs collections that the sync
    and reconciliation endpoints already write to."""
    recon_cursor = db.reconciliation_runs.find({}, {"_id": 0}).sort("run_at", -1).limit(20)
    recon_runs = [doc async for doc in recon_cursor]

    sync_pipeline = [
        {"$sort": {"synced_at": -1}},
        {"$limit": 50},
        {"$project": {"_id": 0}},
    ]
    sync_entries = [doc async for doc in db.qbo_sync_log.aggregate(sync_pipeline)]
    sync_summary_pipeline = [
        {"$group": {"_id": "$qbo_entity_type", "count": {"$sum": 1}}},
    ]
    sync_summary_by_type = {
        doc["_id"] or "unknown": doc["count"] async for doc in db.qbo_sync_log.aggregate(sync_summary_pipeline)
    }
    sync_total = await db.qbo_sync_log.count_documents({})
    sync_success = await db.qbo_sync_log.count_documents({"status": "success"})

    return {
        "reconciliation_runs": recon_runs,
        "sync_log": sync_entries,
        "sync_summary": {
            "total": sync_total,
            "success": sync_success,
            "shown": len(sync_entries),
            "by_qbo_type": sync_summary_by_type,
        },
    }
