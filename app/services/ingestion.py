"""
Ingestion orchestration.

This is the only layer that talks to both MongoDB and the pure business
logic in normalization.py / deduplication.py / classification.py. It is
responsible for:
    1. Storing every raw row untouched in `raw_transactions`, regardless of
       whether it can be processed.
    2. Normalizing each row; rows that fail normalization are flagged
       (`processing_status = error`) rather than dropped.
    3. Running duplicate detection against transactions already canonical in
       the DB (so re-uploading the same file, or an overlapping file, is
       safe).
    4. Running deterministic classification, then Gemini for anything
       unmatched, then leaving it for manual review if neither succeeds.
"""
import logging
import uuid
from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.models.enums import (
    ClassificationSource,
    DuplicateStatus,
    ProcessingStatus,
    ReviewStatus,
)
from app.services import classification
from app.services.deduplication import decide_duplicate_status
from app.services.gemini_classifier import GeminiClassificationError, classify_with_gemini
from app.services.normalization import ColumnMapping, normalize_row

logger = logging.getLogger(__name__)


async def ingest_rows(
    db: AsyncIOMotorDatabase,
    *,
    source_file: str,
    rows: list[dict[str, Any]],
    mapping: ColumnMapping,
    file_hash: str | None = None,
    sheet_name: str | None = None,
    use_gemini: bool = True,
) -> dict:
    """Ingests a batch of raw rows. Returns a summary dict."""
    import_batch_id = str(uuid.uuid4())
    await db.import_batches.insert_one({
        "_id": import_batch_id,
        "source_file": source_file,
        "file_hash": file_hash,
        "sheet_name": sheet_name,
        "row_count": len(rows),
        "created_at": datetime.utcnow(),
        "column_mapping": mapping.__dict__,
    })

    summary = {
        "import_batch_id": import_batch_id,
        "rows_ingested": 0,
        "rows_errored": 0,
        "canonical_created": 0,
        "duplicates_found": 0,
        "needs_review": 0,
    }

    for row_num, raw_record in enumerate(rows, start=1):
        raw_doc = {
            "_id": f"{import_batch_id}:{row_num}",
            "import_batch_id": import_batch_id,
            "source_file": source_file,
            "source_row_number": row_num,
            "raw_record": raw_record,
            "ingested_at": datetime.utcnow(),
            "processing_status": ProcessingStatus.PENDING,
        }
        await db.raw_transactions.insert_one(raw_doc)
        summary["rows_ingested"] += 1

        result = normalize_row(raw_record, mapping)
        if not result.ok:
            await db.raw_transactions.update_one(
                {"_id": raw_doc["_id"]},
                {"$set": {"processing_status": ProcessingStatus.ERROR, "processing_error": "; ".join(result.errors)}},
            )
            summary["rows_errored"] += 1
            continue

        normalized = result.normalized
        txn_doc = await _create_or_link_transaction(db, raw_doc["_id"], normalized, use_gemini=use_gemini)

        await db.raw_transactions.update_one(
            {"_id": raw_doc["_id"]},
            {"$set": {
                "processing_status": ProcessingStatus.PROCESSED,
                "normalized_transaction_id": txn_doc["_id"],
            }},
        )

        if txn_doc["duplicate_status"] == DuplicateStatus.CANONICAL:
            summary["canonical_created"] += 1
        else:
            summary["duplicates_found"] += 1
        if txn_doc["review_status"] == ReviewStatus.NEEDS_REVIEW:
            summary["needs_review"] += 1

    return summary


async def _create_or_link_transaction(
    db: AsyncIOMotorDatabase, raw_id: str, normalized: dict, *, use_gemini: bool
) -> dict:
    bank_account = normalized["bank_account"]
    bank_txn_id = normalized["bank_transaction_id"]
    fingerprint = normalized["fingerprint_hash"]

    existing_by_key: dict[tuple[str, str], str] = {}
    existing_by_fingerprint: dict[str, str] = {}

    if bank_txn_id:
        existing = await db.transactions.find_one({
            "bank_account": bank_account,
            "bank_transaction_id": bank_txn_id,
            "duplicate_status": DuplicateStatus.CANONICAL,
        })
        if existing:
            existing_by_key[(bank_account, bank_txn_id)] = existing["_id"]
    else:
        existing = await db.transactions.find_one({
            "fingerprint_hash": fingerprint,
            "duplicate_status": DuplicateStatus.CANONICAL,
        })
        if existing:
            existing_by_fingerprint[fingerprint] = existing["_id"]

    decision = decide_duplicate_status(
        bank_account=bank_account,
        bank_transaction_id=bank_txn_id,
        fingerprint_hash=fingerprint,
        existing_by_key=existing_by_key,
        existing_by_fingerprint=existing_by_fingerprint,
    )

    txn_id = str(uuid.uuid4())
    duplicate_status = (
        DuplicateStatus.CANONICAL
        if decision.status == DuplicateStatus.NOT_DUPLICATE
        else decision.status
    )

    doc = {
        "_id": txn_id,
        "raw_record_ids": [raw_id],
        "bank_transaction_id": bank_txn_id,
        "fingerprint_hash": fingerprint,
        "duplicate_status": duplicate_status,
        "duplicate_of_transaction_id": decision.duplicate_of_transaction_id,
        "transaction_date": normalized["transaction_date"].isoformat(),
        "posted_date": normalized["posted_date"].isoformat() if normalized["posted_date"] else None,
        "amount_cents": normalized["amount_cents"],
        "currency": normalized["currency"],
        "bank_account": bank_account,
        "description_original": normalized["description_original"],
        "description_normalized": normalized["description_normalized"],
        "transaction_type": None,
        "counterparty": None,
        "qbo_account_number": None,
        "classification_confidence": None,
        "classification_explanation": None,
        "classification_source": None,
        "classification_history": [],
        "review_status": ReviewStatus.NEEDS_REVIEW,
        "validation_errors": [],
        "qbo": {"qbo_sync_status": "not_synced", "qbo_attempt_count": 0},
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    if duplicate_status == DuplicateStatus.CANONICAL:
        await _classify(db, doc, use_gemini=use_gemini)

    try:
        await db.transactions.insert_one(doc)
    except DuplicateKeyError:
        # Race condition safety net: the unique index is the ultimate
        # source of truth. If two rows raced past the decide_duplicate_status
        # check, re-fetch and mark this one an exact duplicate instead of
        # crashing the whole import.
        existing = await db.transactions.find_one({
            "bank_account": bank_account, "bank_transaction_id": bank_txn_id,
        })
        doc["duplicate_status"] = DuplicateStatus.EXACT_DUPLICATE
        doc["duplicate_of_transaction_id"] = existing["_id"] if existing else None
        doc["_id"] = str(uuid.uuid4())
        await db.transactions.insert_one(doc)

    return doc


async def _classify(db: AsyncIOMotorDatabase, doc: dict, *, use_gemini: bool) -> None:
    """Applies, in order: a learned correction rule, deterministic rules,
    then (optionally) Gemini. Anything still unresolved is left as
    needs_review with no classification, per "flag records that cannot be
    processed safely instead of silently dropping them."
    """
    description = doc["description_normalized"]
    amount_cents = doc["amount_cents"]

    rule = await _find_learned_rule(db, description)
    if rule:
        _apply_result(doc, {
            "transaction_type": rule["transaction_type"],
            "counterparty": rule.get("counterparty"),
            "account_number": rule["account_number"],
            "confidence": 0.97,
            "explanation": f"Matched learned rule from a prior reviewer correction ({rule['match_value']})",
            "source": ClassificationSource.LEARNED_RULE,
        })
        await db.classification_rules.update_one({"_id": rule["_id"]}, {"$inc": {"times_applied": 1}})
        return

    result = classification.classify_deterministic(description, amount_cents)
    if result:
        _apply_result(doc, {
            "transaction_type": result.transaction_type,
            "counterparty": result.counterparty,
            "account_number": result.account_number,
            "confidence": result.confidence,
            "explanation": result.explanation,
            "source": ClassificationSource.RULE,
        })
        # High-confidence deterministic matches are safe to auto-approve;
        # everything else waits for a human, per the assignment.
        if result.confidence >= 0.95:
            doc["review_status"] = ReviewStatus.APPROVED
        return

    if use_gemini:
        try:
            gemini_result = await classify_with_gemini(description, amount_cents)
            _apply_result(doc, gemini_result)
            return
        except GeminiClassificationError as exc:
            logger.warning("Gemini classification failed for %r: %s", description, exc)

    # Neither rules nor Gemini could confidently classify this row -- leave
    # it fully visible in the review queue rather than guessing.
    doc["review_status"] = ReviewStatus.NEEDS_REVIEW


async def _find_learned_rule(db: AsyncIOMotorDatabase, description_normalized: str) -> dict | None:
    upper = description_normalized.upper()
    cursor = db.classification_rules.find({"active": True})
    async for rule in cursor:
        if rule["match_type"] in ("description_contains", "counterparty_contains"):
            if rule["match_value"].upper() in upper:
                return rule
        elif rule["match_type"] == "exact_description":
            if rule["match_value"].upper() == upper:
                return rule
    return None


def _apply_result(doc: dict, result: dict) -> None:
    doc["transaction_type"] = result["transaction_type"]
    doc["counterparty"] = result.get("counterparty")
    doc["qbo_account_number"] = result["account_number"]
    doc["classification_confidence"] = result["confidence"]
    doc["classification_explanation"] = result["explanation"]
    doc["classification_source"] = result.get("source", ClassificationSource.RULE)
    doc["classification_history"].append({
        "changed_at": datetime.utcnow().isoformat(),
        "source": result.get("source", ClassificationSource.RULE),
        "transaction_type": result["transaction_type"],
        "account_number": result["account_number"],
        "counterparty": result.get("counterparty"),
        "confidence": result["confidence"],
        "explanation": result["explanation"],
        "changed_by": "system",
    })
