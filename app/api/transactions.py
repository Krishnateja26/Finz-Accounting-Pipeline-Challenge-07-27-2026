from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.database import get_db
from app.models.enums import ClassificationSource, DuplicateStatus, MatchType, ReviewStatus
from app.services.classification import CHART_OF_ACCOUNTS

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


@router.get("")
async def list_transactions(
    review_status: str | None = None,
    duplicate_status: str | None = None,
    month: str | None = None,  # "2026-04"
    bank_account: str | None = None,
    max_confidence: float | None = None,
    confidence_range: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    query: dict = {}
    if review_status:
        query["review_status"] = review_status
    if duplicate_status:
        query["duplicate_status"] = duplicate_status
    if bank_account:
        query["bank_account"] = bank_account
    if month:
        query["transaction_date"] = {"$regex": f"^{month}"}
    if confidence_range == "high":
        query["classification_confidence"] = {"$gt": 0.9}
    elif confidence_range == "mid":
        query["classification_confidence"] = {"$gte": 0.7, "$lte": 0.9}
    elif confidence_range == "low":
        query["$or"] = [
            {"classification_confidence": {"$lt": 0.7}},
            {"classification_confidence": None},
            {"classification_confidence": {"$exists": False}},
        ]
    elif max_confidence is not None:
        query["classification_confidence"] = {"$lte": max_confidence}

    cursor = db.transactions.find(query).sort("transaction_date", 1)
    return [doc async for doc in cursor]


@router.get("/meta/counts")
async def transaction_counts(db: AsyncIOMotorDatabase = Depends(get_db)):
    docs = [doc async for doc in db.transactions.find({})]
    counts = {
        "total": len(docs),
        "by_review_status": {},
        "by_duplicate_status": {},
        "by_transaction_type": {},
        "by_classification_source": {},
    }

    for doc in docs:
        review_status = doc.get("review_status") or "unknown"
        duplicate_status = doc.get("duplicate_status") or "unknown"
        transaction_type = doc.get("transaction_type") or "unclassified"
        classification_source = doc.get("classification_source") or "none"

        counts["by_review_status"][review_status] = counts["by_review_status"].get(review_status, 0) + 1
        counts["by_duplicate_status"][duplicate_status] = counts["by_duplicate_status"].get(duplicate_status, 0) + 1
        counts["by_transaction_type"][transaction_type] = counts["by_transaction_type"].get(transaction_type, 0) + 1
        counts["by_classification_source"][classification_source] = counts["by_classification_source"].get(classification_source, 0) + 1

    return counts


@router.get("/meta/chart-of-accounts")
async def chart_of_accounts():
    return CHART_OF_ACCOUNTS


@router.get("/{transaction_id}")
async def get_transaction(transaction_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    doc = await db.transactions.find_one({"_id": transaction_id})
    if not doc:
        raise HTTPException(404, "Transaction not found")
    raw_docs = [
        await db.raw_transactions.find_one({"_id": rid}) for rid in doc.get("raw_record_ids", [])
    ]
    doc["raw_records"] = raw_docs
    return doc


class CorrectionRequest(BaseModel):
    transaction_type: str
    account_number: str
    counterparty: str | None = None
    reviewer: str = "reviewer"
    save_as_rule: bool = False
    rule_match_value: str | None = None  # e.g. "FLEET AUTO CARE"


@router.post("/{transaction_id}/correct")
async def correct_transaction(
    transaction_id: str, body: CorrectionRequest, db: AsyncIOMotorDatabase = Depends(get_db)
):
    if body.account_number not in CHART_OF_ACCOUNTS:
        raise HTTPException(400, f"Unknown account number {body.account_number}")

    doc = await db.transactions.find_one({"_id": transaction_id})
    if not doc:
        raise HTTPException(404, "Transaction not found")

    history_entry = {
        "changed_at": datetime.utcnow().isoformat(),
        "source": ClassificationSource.MANUAL,
        "transaction_type": body.transaction_type,
        "account_number": body.account_number,
        "counterparty": body.counterparty,
        "confidence": 1.0,
        "explanation": "Manually corrected by reviewer",
        "changed_by": body.reviewer,
    }

    await db.transactions.update_one(
        {"_id": transaction_id},
        {
            "$set": {
                "transaction_type": body.transaction_type,
                "qbo_account_number": body.account_number,
                "counterparty": body.counterparty,
                "classification_confidence": 1.0,
                "classification_explanation": "Manually corrected by reviewer",
                "classification_source": ClassificationSource.MANUAL,
                "review_status": ReviewStatus.APPROVED,
                "reviewed_by": body.reviewer,
                "reviewed_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            },
            "$push": {"classification_history": history_entry},
        },
    )

    if body.save_as_rule and body.rule_match_value:
        await db.classification_rules.update_one(
            {"match_type": MatchType.DESCRIPTION_CONTAINS, "match_value": body.rule_match_value.upper()},
            {
                "$set": {
                    "account_number": body.account_number,
                    "transaction_type": body.transaction_type,
                    "counterparty": body.counterparty,
                    "created_from_correction": True,
                    "approved_by": body.reviewer,
                    "active": True,
                },
                "$setOnInsert": {"created_at": datetime.utcnow(), "times_applied": 0},
            },
            upsert=True,
        )

    return await db.transactions.find_one({"_id": transaction_id})


class ApproveRequest(BaseModel):
    reviewer: str = "reviewer"


@router.post("/bulk/approve-classified")
async def bulk_approve_classified(
    body: ApproveRequest, db: AsyncIOMotorDatabase = Depends(get_db)
):
    """Approve canonical transactions that already have a classification.

    This keeps true manual-review cases visible: exact duplicates,
    unclassified rows, and rows missing a QBO account are not touched.
    """
    query = {
        "duplicate_status": DuplicateStatus.CANONICAL,
        "review_status": ReviewStatus.NEEDS_REVIEW,
        "transaction_type": {"$ne": None},
        "qbo_account_number": {"$ne": None},
    }
    result = await db.transactions.update_many(
        query,
        {"$set": {
            "review_status": ReviewStatus.APPROVED,
            "reviewed_by": body.reviewer,
            "reviewed_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }},
    )
    return {"approved_count": result.modified_count}


@router.post("/{transaction_id}/approve")
async def approve_transaction(
    transaction_id: str, body: ApproveRequest, db: AsyncIOMotorDatabase = Depends(get_db)
):
    doc = await db.transactions.find_one({"_id": transaction_id})
    if not doc:
        raise HTTPException(404, "Transaction not found")
    if not doc.get("qbo_account_number"):
        raise HTTPException(400, "Cannot approve a transaction with no classification -- correct it first")

    await db.transactions.update_one(
        {"_id": transaction_id},
        {"$set": {
            "review_status": ReviewStatus.APPROVED,
            "reviewed_by": body.reviewer,
            "reviewed_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }},
    )
    return await db.transactions.find_one({"_id": transaction_id})
