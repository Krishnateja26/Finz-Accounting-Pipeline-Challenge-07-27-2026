"""
MongoDB connection handling.

Collections used by this application:
    import_batches        - one document per uploaded file
    raw_transactions       - untouched source rows, one per uploaded row
    transactions            - normalized / classified / dedup'd transactions
    classification_rules    - reusable rules learned from reviewer corrections
    qbo_sync_log             - append-only log of every QuickBooks API attempt
    reconciliation_runs     - stored output of each reconciliation run

Business logic (normalization, deduplication, classification, P&L math) is
kept in app/services as pure functions that do NOT talk to Mongo directly,
so it can be unit tested without a running database. This module is only
responsible for wiring up the async client and indexes.
"""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from mongomock_motor import AsyncMongoMockClient

from app.config import get_settings
from app.models.enums import DuplicateStatus

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        settings = get_settings()
        if settings.mongo_uri.startswith("mongomock://"):
            _client = AsyncMongoMockClient()
        else:
            _client = AsyncIOMotorClient(settings.mongo_uri)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        settings = get_settings()
        _db = get_client()[settings.mongo_db_name]
    return _db


async def ensure_indexes() -> None:
    """Create the indexes that enforce our duplicate-prevention guarantees.

    These indexes are the real duplicate-prevention control, not just an
    optimization -- e.g. the unique index on (bank_account, bank_transaction_id)
    is what makes a second upload of the same file a no-op instead of a
    silent double count.
    """
    db = get_db()

    await db.raw_transactions.create_index(
        [("import_batch_id", 1), ("source_row_number", 1)], unique=True
    )
    await db.raw_transactions.create_index("processing_status")

    await db.transactions.create_index(
        [("bank_account", 1), ("bank_transaction_id", 1)],
        unique=True,
        partialFilterExpression={
            "bank_transaction_id": {"$type": "string"},
            "duplicate_status": DuplicateStatus.CANONICAL,
        },
        name="uniq_bank_account_bank_txn_id",
    )
    await db.transactions.create_index("fingerprint_hash")
    await db.transactions.create_index("transaction_date")
    await db.transactions.create_index("duplicate_status")
    await db.transactions.create_index("review_status")
    await db.transactions.create_index("qbo_sync_status")

    await db.classification_rules.create_index(
        [("match_type", 1), ("match_value", 1)], unique=True
    )

    await db.qbo_sync_log.create_index("qbo_request_id", unique=True)
    await db.qbo_sync_log.create_index("transaction_id")

    await db.reconciliation_runs.create_index([("period_start", 1), ("period_end", 1)])


async def close_client() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None
