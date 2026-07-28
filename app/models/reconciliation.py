from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MatchType, TransactionType


class ClassificationRule(BaseModel):
    """A reusable rule, either shipped with the app or learned from a
    reviewer's correction (`created_from_correction=True`)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    match_type: MatchType
    match_value: str  # matched case-insensitively, substring or exact per match_type
    account_number: str
    transaction_type: TransactionType
    counterparty: str | None = None
    created_from_correction: bool = False
    approved_by: str | None = None
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    times_applied: int = 0


class ReconciliationLine(BaseModel):
    period_label: str
    account_number: str | None = None
    account_name: str
    app_amount_cents: int
    qbo_amount_cents: int
    difference_cents: int
    is_reconciled: bool
    explanation: str | None = None


class ReconciliationRun(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    period_start: date
    period_end: date
    period_label: str
    lines: list[ReconciliationLine] = Field(default_factory=list)
    fully_reconciled: bool
    run_at: datetime = Field(default_factory=datetime.utcnow)
