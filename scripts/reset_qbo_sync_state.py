import asyncio
import uuid
from datetime import datetime

from app.database import close_client, get_db
from app.models.enums import DuplicateStatus, ReviewStatus, SyncStatus
from app.api.quickbooks import SYNC_NAMESPACE_DOC_ID


async def main() -> None:
    db = get_db()
    sync_namespace = str(uuid.uuid4())
    deleted_log = await db.qbo_sync_log.delete_many({})
    await db.app_settings.update_one(
        {"_id": SYNC_NAMESPACE_DOC_ID},
        {"$set": {"value": sync_namespace, "rotated_at": datetime.utcnow()}},
        upsert=True,
    )
    result = await db.transactions.update_many(
        {
            "duplicate_status": DuplicateStatus.CANONICAL,
            "review_status": ReviewStatus.APPROVED,
        },
        {
            "$set": {
                "qbo.qbo_sync_status": SyncStatus.NOT_SYNCED,
                "qbo.qbo_attempt_count": 0,
                "qbo.qbo_last_error": None,
            },
            "$unset": {
                "qbo.qbo_entity_type": "",
                "qbo.qbo_transaction_id": "",
                "qbo.qbo_request_id": "",
                "qbo.qbo_synced_at": "",
            },
        },
    )
    counts = {
        doc["_id"]: doc["count"]
        async for doc in db.transactions.aggregate([
            {"$group": {"_id": "$qbo.qbo_sync_status", "count": {"$sum": 1}}},
        ])
    }
    print({
        "sync_log_deleted": deleted_log.deleted_count,
        "transactions_reset": result.modified_count,
        "sync_namespace": sync_namespace,
        "sync_counts": counts,
    })
    await close_client()


if __name__ == "__main__":
    asyncio.run(main())
