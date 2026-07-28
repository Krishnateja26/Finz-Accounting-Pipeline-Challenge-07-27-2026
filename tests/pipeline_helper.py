"""
Runs normalization -> dedup -> classification entirely in memory, mirroring
the decisions app/services/ingestion.py makes against MongoDB. Used by tests
so the core accounting logic can be verified without a running database.
"""
from app.models.enums import DuplicateStatus
from app.services.classification import classify_deterministic
from app.services.deduplication import decide_duplicate_status
from app.services.normalization import ColumnMapping, normalize_row


def run_pipeline(rows: list[dict], mapping: ColumnMapping) -> list[dict]:
    existing_by_key: dict[tuple[str, str], str] = {}
    existing_by_fingerprint: dict[str, str] = {}
    out = []

    for i, raw in enumerate(rows):
        result = normalize_row(raw, mapping)
        if not result.ok:
            out.append({"_id": f"txn_{i}", "raw": raw, "normalization_errors": result.errors, "duplicate_status": None})
            continue

        n = result.normalized
        decision = decide_duplicate_status(
            bank_account=n["bank_account"],
            bank_transaction_id=n["bank_transaction_id"],
            fingerprint_hash=n["fingerprint_hash"],
            existing_by_key=existing_by_key,
            existing_by_fingerprint=existing_by_fingerprint,
        )
        txn_id = f"txn_{i}"
        n["_id"] = txn_id
        n["duplicate_status"] = decision.status
        n["duplicate_of_transaction_id"] = decision.duplicate_of_transaction_id

        if decision.status == DuplicateStatus.NOT_DUPLICATE:
            n["duplicate_status"] = DuplicateStatus.CANONICAL
            if n["bank_transaction_id"]:
                existing_by_key[(n["bank_account"], n["bank_transaction_id"])] = txn_id
            else:
                existing_by_fingerprint[n["fingerprint_hash"]] = txn_id

            clf = classify_deterministic(n["description_normalized"], n["amount_cents"])
            if clf:
                n["transaction_type"] = clf.transaction_type
                n["qbo_account_number"] = clf.account_number
                n["counterparty"] = clf.counterparty
                n["classification_confidence"] = clf.confidence
                n["review_status"] = "approved" if clf.confidence >= 0.95 else "needs_review"
            else:
                n["transaction_type"] = None
                n["qbo_account_number"] = None
                n["classification_confidence"] = None
                n["review_status"] = "needs_review"

        n["transaction_date"] = n["transaction_date"].isoformat()
        n["posted_date"] = n["posted_date"].isoformat() if n["posted_date"] else None
        out.append(n)

    return out
