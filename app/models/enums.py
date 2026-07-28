from enum import StrEnum


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    ERROR = "error"


class DuplicateStatus(StrEnum):
    CANONICAL = "canonical"
    EXACT_DUPLICATE = "exact_duplicate"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    NOT_DUPLICATE = "not_duplicate"


class TransactionType(StrEnum):
    REVENUE = "revenue"
    COGS = "cogs"
    OPERATING_EXPENSE = "operating_expense"
    REFUND = "refund"
    TRANSFER = "transfer"
    OWNER_CONTRIBUTION = "owner_contribution"
    OWNER_DISTRIBUTION = "owner_distribution"
    FIXED_ASSET_PURCHASE = "fixed_asset_purchase"
    UNKNOWN = "unknown"


# Transaction types excluded from the P&L entirely (balance-sheet / equity /
# internal movements, per the assignment's accounting rules).
NON_PNL_TYPES = {
    TransactionType.TRANSFER,
    TransactionType.OWNER_CONTRIBUTION,
    TransactionType.OWNER_DISTRIBUTION,
    TransactionType.FIXED_ASSET_PURCHASE,
    TransactionType.UNKNOWN,
}


class ReviewStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    VALIDATION_ERROR = "validation_error"


class SyncStatus(StrEnum):
    NOT_SYNCED = "not_synced"
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


class ClassificationSource(StrEnum):
    RULE = "deterministic_rule"
    LEARNED_RULE = "learned_rule"
    GEMINI = "gemini"
    MANUAL = "manual"


class MatchType(StrEnum):
    DESCRIPTION_CONTAINS = "description_contains"
    COUNTERPARTY_CONTAINS = "counterparty_contains"
    EXACT_DESCRIPTION = "exact_description"
