import asyncio
import uuid
from datetime import datetime

from app.database import close_client, ensure_indexes, get_db
from app.api.quickbooks import SYNC_NAMESPACE_DOC_ID


COLLECTIONS_TO_CLEAR = [
    "import_batches",
    "raw_transactions",
    "transactions",
    "classification_rules",
    "qbo_sync_log",
    "reconciliation_runs",
]


async def main() -> None:
    db = get_db()
    sync_namespace = str(uuid.uuid4())
    before = {name: await db[name].count_documents({}) for name in COLLECTIONS_TO_CLEAR}
    deleted = {}
    for name in COLLECTIONS_TO_CLEAR:
        result = await db[name].delete_many({})
        deleted[name] = result.deleted_count
    await db.app_settings.update_one(
        {"_id": SYNC_NAMESPACE_DOC_ID},
        {"$set": {"value": sync_namespace, "rotated_at": datetime.utcnow()}},
        upsert=True,
    )
    await ensure_indexes()
    after = {name: await db[name].count_documents({}) for name in COLLECTIONS_TO_CLEAR}
    print({
        "before": before,
        "deleted": deleted,
        "after": after,
        "sync_namespace": sync_namespace,
        "preserved": ["qbo_oauth"],
    })
    await close_client()


if __name__ == "__main__":
    asyncio.run(main())
