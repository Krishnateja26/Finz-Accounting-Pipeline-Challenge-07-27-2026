import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.quickbooks import SYNC_NAMESPACE_DOC_ID, TOKEN_DOC_ID
from app.config import get_settings
from app.database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])

RESET_COLLECTIONS = [
    "import_batches",
    "raw_transactions",
    "transactions",
    "classification_rules",
    "qbo_sync_log",
    "reconciliation_runs",
]


@router.post("/reset")
async def reset_app_data(
    db: AsyncIOMotorDatabase = Depends(get_db),
    x_admin_reset_token: str | None = Header(default=None),
):
    settings = get_settings()
    if not settings.admin_reset_token:
        raise HTTPException(403, "Admin reset is not configured")
    if x_admin_reset_token != settings.admin_reset_token:
        raise HTTPException(403, "Invalid admin reset token")

    before = {
        collection: await db[collection].count_documents({})
        for collection in RESET_COLLECTIONS
    }
    deleted = {}
    for collection in RESET_COLLECTIONS:
        result = await db[collection].delete_many({})
        deleted[collection] = result.deleted_count

    oauth_deleted = await db.app_settings.delete_one({"_id": TOKEN_DOC_ID})
    sync_namespace = str(uuid.uuid4())
    await db.app_settings.update_one(
        {"_id": SYNC_NAMESPACE_DOC_ID},
        {"$set": {"value": sync_namespace, "rotated_at": datetime.now(UTC)}},
        upsert=True,
    )

    after = {
        collection: await db[collection].count_documents({})
        for collection in RESET_COLLECTIONS
    }

    return {
        "before": before,
        "deleted": deleted,
        "after": after,
        "qbo_oauth_deleted": oauth_deleted.deleted_count,
        "sync_namespace": sync_namespace,
    }
