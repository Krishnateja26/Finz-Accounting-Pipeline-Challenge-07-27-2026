"""
Duplicate detection.

Layered strategy (per assignment):
1. Primary key: (bank_account, bank_transaction_id) when a transaction ID is
   present. This is enforced by a unique index in Mongo AND checked here so
   we can return a friendly "exact_duplicate, here is the canonical id"
   result instead of a raw DB error.
2. Fallback fingerprint: sha256(bank_account, date, amount, normalized
   description) when there is no transaction ID.
3. Fuzzy candidates (same amount + same/near date, different description) are
   never auto-merged -- they come back as POSSIBLE_DUPLICATE and require
   human review, because two legitimate payroll runs can look identical.
"""
from dataclasses import dataclass

from app.models.enums import DuplicateStatus


@dataclass
class DedupDecision:
    status: DuplicateStatus
    duplicate_of_transaction_id: str | None = None
    reason: str | None = None


def decide_duplicate_status(
    *,
    bank_account: str,
    bank_transaction_id: str | None,
    fingerprint_hash: str,
    existing_by_key: dict[tuple[str, str], str],  # (bank_account, bank_txn_id) -> canonical txn id
    existing_by_fingerprint: dict[str, str],  # fingerprint -> canonical txn id
    existing_fuzzy_fingerprints: dict[str, list[str]] | None = None,
) -> DedupDecision:
    """Given the incoming row's identifying keys and lookup tables of what's
    already canonical, decide what to do with this row.

    `existing_by_key` / `existing_by_fingerprint` should reflect only
    CANONICAL transactions, so a second exact duplicate of an existing
    duplicate still correctly points back at the original canonical id.
    """
    if bank_transaction_id:
        key = (bank_account, bank_transaction_id)
        if key in existing_by_key:
            return DedupDecision(
                status=DuplicateStatus.EXACT_DUPLICATE,
                duplicate_of_transaction_id=existing_by_key[key],
                reason=f"Same bank_account+bank_transaction_id already canonical ({bank_transaction_id})",
            )
        return DedupDecision(status=DuplicateStatus.NOT_DUPLICATE)

    # No transaction ID supplied -- fall back to the content fingerprint.
    if fingerprint_hash in existing_by_fingerprint:
        return DedupDecision(
            status=DuplicateStatus.EXACT_DUPLICATE,
            duplicate_of_transaction_id=existing_by_fingerprint[fingerprint_hash],
            reason="Same bank_account+date+amount+normalized description already canonical",
        )

    return DedupDecision(status=DuplicateStatus.NOT_DUPLICATE)
