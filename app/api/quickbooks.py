import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.config import get_settings
from app.database import get_db
from app.models.enums import DuplicateStatus, ReviewStatus, SyncStatus, TransactionType
from app.services.qbo_client import QboApiError, QboAuthError, QboClient, build_request_id

router = APIRouter(prefix="/api/quickbooks", tags=["quickbooks"])
TOKEN_DOC_ID = "qbo_oauth"
SYNC_NAMESPACE_DOC_ID = "qbo_sync_namespace"
CHART_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "chart_of_accounts.json"
logger = logging.getLogger(__name__)


async def _qbo_client_from_db(db: AsyncIOMotorDatabase) -> QboClient:
    token_doc = await db.app_settings.find_one({"_id": TOKEN_DOC_ID})
    if token_doc:
        return QboClient(
            realm_id=token_doc.get("realm_id"),
            access_token=token_doc.get("access_token"),
        )
    return QboClient()


async def _qbo_sync_namespace(db: AsyncIOMotorDatabase) -> str:
    """Namespace QBO request IDs so a sandbox reset can force fresh posts.

    QuickBooks can remember idempotency request IDs even after the sandbox's
    visible transactions are cleared. Keeping our own namespace lets local
    retries stay safe, while reset_qbo_sync_state can rotate the namespace
    before replaying into a freshly reset sandbox.
    """
    doc = await db.app_settings.find_one({"_id": SYNC_NAMESPACE_DOC_ID})
    if doc and doc.get("value"):
        return doc["value"]

    value = str(uuid.uuid4())
    await db.app_settings.update_one(
        {"_id": SYNC_NAMESPACE_DOC_ID},
        {"$set": {"value": value, "created_at": datetime.utcnow()}},
        upsert=True,
    )
    return value


@router.get("/connect")
async def connect():
    """Kicks off the OAuth 2.0 authorization-code flow against the sandbox."""
    settings = get_settings()
    if not settings.qbo_client_id:
        raise HTTPException(400, "QBO_CLIENT_ID is not configured -- see .env.example")
    state = str(uuid.uuid4())
    client = QboClient()
    return RedirectResponse(client.authorization_url(state))


@router.get("/callback")
async def callback(code: str, realmId: str, state: str | None = None, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Intuit redirects here after the user approves the sandbox connection.
    Exchanges the auth code for tokens. In production, tokens would be
    written to a secrets store, not returned in the response body -- shown
    here for local/dev clarity only."""
    client = QboClient()
    try:
        tokens = await client.exchange_code_for_tokens(code, realmId)
    except QboAuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token:
        raise HTTPException(400, f"Token exchange succeeded but no access token was returned: {tokens}")

    token_update = {
        "realm_id": realmId,
        "access_token": access_token,
        "connected_at": datetime.utcnow(),
    }
    if refresh_token:
        token_update["refresh_token"] = refresh_token

    await db.app_settings.update_one(
        {"_id": TOKEN_DOC_ID},
        {"$set": token_update},
        upsert=True,
    )
    return RedirectResponse("/reconciliation?connected=1")


class SyncRequest(BaseModel):
    month: str | None = None  # "2026-04", or None for all approved


@router.post("/setup-chart-of-accounts")
async def setup_chart_of_accounts(db: AsyncIOMotorDatabase = Depends(get_db)):
    client = await _qbo_client_from_db(db)
    if not client.realm_id:
        raise HTTPException(400, "QuickBooks is not connected -- connect the sandbox first")

    accounts = json.loads(CHART_FILE.read_text())
    results = {"created": [], "already_exists": [], "failed": []}

    for account in accounts:
        name = account["Account Name"]
        try:
            existing = await client.find_account_by_name(name)
            if not existing and account.get("Account No.") == "3000":
                equity_accounts = await client.find_accounts_by_type("Equity")
                existing = equity_accounts[0] if equity_accounts else None
            if existing:
                results["already_exists"].append(name)
                continue

            await client.create_account(
                name=name,
                account_type=account["QBO Account Type"],
                account_subtype=account.get("Suggested Detail Type") or None,
            )
            results["created"].append(name)
        except (QboApiError, QboAuthError) as exc:
            results["failed"].append({"account": name, "error": str(exc)})

    return results


@router.post("/sync")
async def sync_transactions(body: SyncRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Syncs every approved, canonical, not-yet-synced transaction to QBO.
    Safe to call repeatedly: already-synced transactions are skipped, and
    each POST uses a stable request id so a mid-flight network failure can't
    create a duplicate on retry.
    """
    settings = get_settings()
    query = {
        "duplicate_status": DuplicateStatus.CANONICAL,
        "review_status": ReviewStatus.APPROVED,
        "qbo.qbo_sync_status": {"$in": [SyncStatus.NOT_SYNCED, SyncStatus.FAILED]},
    }
    if body.month:
        query["transaction_date"] = {"$regex": f"^{body.month}"}

    client = await _qbo_client_from_db(db)
    sync_namespace = await _qbo_sync_namespace(db)
    results = {"synced": 0, "already_synced_skipped": 0, "skipped_non_posting": 0, "failed": 0, "errors": []}

    cursor = db.transactions.find(query)
    async for txn in cursor:
        if txn.get("transaction_type") == TransactionType.TRANSFER:
            await db.transactions.update_one(
                {"_id": txn["_id"]},
                {"$set": {
                    "qbo.qbo_sync_status": SyncStatus.SYNCED,
                    "qbo.qbo_entity_type": "SkippedInternalTransfer",
                    "qbo.qbo_last_error": None,
                    "qbo.qbo_synced_at": datetime.utcnow(),
                }},
            )
            results["skipped_non_posting"] += 1
            continue

        if not client.realm_id:
            raise HTTPException(400, "QuickBooks realm ID is missing -- reconnect the sandbox")
        stable_txn_key = (
            f"{txn.get('bank_account')}|{txn.get('bank_transaction_id')}"
            if txn.get("bank_transaction_id")
            else f"{txn.get('bank_account')}|{txn.get('fingerprint_hash')}"
        )
        request_id = build_request_id(client.realm_id, f"{sync_namespace}|{stable_txn_key}")

        # Check our own log first -- if we already have a successful attempt
        # for this request id, never send another create request.
        prior = await db.qbo_sync_log.find_one({"qbo_request_id": request_id, "status": "success"})
        if prior:
            await db.transactions.update_one(
                {"_id": txn["_id"]},
                {"$set": {"qbo.qbo_sync_status": SyncStatus.SYNCED, "qbo.qbo_transaction_id": prior["qbo_transaction_id"]}},
            )
            results["already_synced_skipped"] += 1
            continue

        try:
            post_result = await client.post_transaction(txn, request_id)
            await db.qbo_sync_log.insert_one({
                "qbo_request_id": request_id,
                "transaction_id": txn["_id"],
                "status": "success",
                "qbo_transaction_id": post_result["qbo_transaction_id"],
                "qbo_entity_type": post_result["qbo_entity_type"],
                "synced_at": datetime.utcnow(),
            })
            await db.transactions.update_one(
                {"_id": txn["_id"]},
                {"$set": {
                    "qbo.qbo_entity_type": post_result["qbo_entity_type"],
                    "qbo.qbo_transaction_id": post_result["qbo_transaction_id"],
                    "qbo.qbo_request_id": request_id,
                    "qbo.qbo_sync_status": SyncStatus.SYNCED,
                    "qbo.qbo_synced_at": datetime.utcnow(),
                }, "$inc": {"qbo.qbo_attempt_count": 1}},
            )
            results["synced"] += 1
        except (QboApiError, QboAuthError) as exc:
            await db.transactions.update_one(
                {"_id": txn["_id"]},
                {"$set": {"qbo.qbo_sync_status": SyncStatus.FAILED, "qbo.qbo_last_error": str(exc)},
                 "$inc": {"qbo.qbo_attempt_count": 1}},
            )
            results["failed"] += 1
            results["errors"].append({"transaction_id": str(txn["_id"]), "error": str(exc)})
        except Exception as exc:
            logger.exception("Unexpected QuickBooks sync failure for transaction %s", txn.get("_id"))
            await db.transactions.update_one(
                {"_id": txn["_id"]},
                {"$set": {"qbo.qbo_sync_status": SyncStatus.FAILED, "qbo.qbo_last_error": str(exc)},
                 "$inc": {"qbo.qbo_attempt_count": 1}},
            )
            results["failed"] += 1
            results["errors"].append({"transaction_id": str(txn["_id"]), "error": str(exc)})

    return results


@router.get("/status")
async def sync_status(db: AsyncIOMotorDatabase = Depends(get_db)):
    pipeline = [{"$group": {"_id": "$qbo.qbo_sync_status", "count": {"$sum": 1}}}]
    counts = {doc["_id"]: doc["count"] async for doc in db.transactions.aggregate(pipeline)}
    token_doc = await db.app_settings.find_one({"_id": TOKEN_DOC_ID})
    return {
        "connected": bool(token_doc and token_doc.get("access_token") and token_doc.get("realm_id")),
        "realm_id": token_doc.get("realm_id") if token_doc else None,
        "sync_counts": counts,
    }
