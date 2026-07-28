from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ClassificationSource,
    DuplicateStatus,
    ReviewStatus,
    SyncStatus,
    TransactionType,
)


class ClassificationHistoryEntry(BaseModel):
    changed_at: datetime = Field(default_factory=datetime.utcnow)
    source: ClassificationSource
    transaction_type: TransactionType
    account_number: str
    counterparty: str | None = None
    confidence: float | None = None
    explanation: str | None = None
    changed_by: str | None = None  # reviewer name/id, or "system"


class QboSyncResult(BaseModel):
    qbo_entity_type: str | None = None
    qbo_transaction_id: str | None = None
    qbo_request_id: str | None = None
    qbo_sync_status: SyncStatus = SyncStatus.NOT_SYNCED
    qbo_synced_at: datetime | None = None
    qbo_attempt_count: int = 0
    qbo_last_error: str | None = None


class Transaction(BaseModel):
    """The normalized, accounting-ready record.

    Money is always stored as integer cents to avoid floating point drift.
    `duplicate_status` distinguishes CANONICAL (the one record that should be
    counted) from EXACT_DUPLICATE / POSSIBLE_DUPLICATE, which are kept but
    excluded from the P&L and from QuickBooks sync.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    raw_record_ids: list[str] = Field(default_factory=list)

    # Identity / dedup
    bank_transaction_id: str | None = None
    fingerprint_hash: str | None = None  # used when bank_transaction_id is missing
    duplicate_status: DuplicateStatus = DuplicateStatus.NOT_DUPLICATE
    duplicate_of_transaction_id: str | None = None

    # Normalized fields
    transaction_date: date
    posted_date: date | None = None
    amount_cents: int  # signed: negative = money out, positive = money in
    currency: str = "USD"
    bank_account: str
    description_original: str
    description_normalized: str

    # Classification
    transaction_type: TransactionType = TransactionType.UNKNOWN
    counterparty: str | None = None
    qbo_account_number: str | None = None
    classification_confidence: float | None = None
    classification_explanation: str | None = None
    classification_source: ClassificationSource | None = None
    classification_history: list[ClassificationHistoryEntry] = Field(default_factory=list)

    # Review workflow
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    # Validation
    validation_errors: list[str] = Field(default_factory=list)

    # QuickBooks
    qbo: QboSyncResult = Field(default_factory=QboSyncResult)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def amount_dollars(self) -> float:
        return self.amount_cents / 100

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, exclude_none=False)
        data.pop("_id", None) if data.get("_id") is None else None
        return data
