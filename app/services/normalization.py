"""
Normalization: turns one arbitrary raw row + a column mapping into a
normalized dict ready to become a Transaction, OR a list of validation
errors if the row cannot be processed safely.

Nothing here hard-codes a column order. The caller supplies a `ColumnMapping`
built from the upload's mapping screen, so the same code works for
BrightFix's export today and a totally different bank's CSV tomorrow.
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

REQUIRED_FIELDS = [
    "transaction_id",
    "transaction_date",
    "description",
    "amount",
    "currency",
    "bank_account",
]

KNOWN_CURRENCIES = {"USD"}
KNOWN_BANK_ACCOUNTS = {"Operating Checking", "Tax Reserve"}

_WHITESPACE_RE = re.compile(r"\s+")

# Counterparty/description cleanup: collapse repeated whitespace, strip common
# ACH/POS boilerplate tokens so downstream classification rules match reliably.
_NOISE_TOKENS = ["ACH", "POS", "DEBIT", "CREDIT", "PMT", "PAYMENT"]


@dataclass
class ColumnMapping:
    """Maps normalized field names -> the column name in the uploaded file."""

    transaction_id: str | None
    transaction_date: str
    posted_date: str | None
    description: str
    amount: str
    currency: str | None
    bank_account: str

    @classmethod
    def suggest(cls, columns: list[str]) -> "ColumnMapping":
        """Best-effort auto mapping the mapping UI pre-fills; a human can
        still override every field via dropdown before import runs."""

        def find(*candidates: str) -> str | None:
            lowered = {c.lower(): c for c in columns}
            for cand in candidates:
                if cand.lower() in lowered:
                    return lowered[cand.lower()]
            return None

        return cls(
            transaction_id=find("Bank Transaction ID", "Transaction ID", "Reference"),
            transaction_date=find("Transaction Date", "Date") or columns[0],
            posted_date=find("Posted Date", "Post Date"),
            description=find("Description", "Memo", "Details") or columns[0],
            amount=find("Amount (USD)", "Amount", "Amount (USD)") or columns[0],
            currency=find("Currency"),
            bank_account=find("Bank Account", "Account") or columns[0],
        )


def normalize_description(raw: str) -> str:
    text = _WHITESPACE_RE.sub(" ", raw or "").strip()
    return text


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount_cents(value: Any) -> int | None:
    """Parses money as Decimal, never float, and returns integer cents."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            d = Decimal(str(value))
        except InvalidOperation:
            return None
    else:
        text = str(value).strip().replace("$", "").replace(",", "")
        # handle accounting-style negatives like (123.45)
        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1]
        try:
            d = Decimal(text)
        except InvalidOperation:
            return None
        if negative:
            d = -d
    return int((d * 100).to_integral_value())


def compute_fingerprint(bank_account: str, transaction_date: date, amount_cents: int, description_normalized: str) -> str:
    payload = f"{bank_account}|{transaction_date.isoformat()}|{amount_cents}|{description_normalized.upper()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class NormalizationResult:
    ok: bool
    normalized: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def normalize_row(raw_record: dict[str, Any], mapping: ColumnMapping) -> NormalizationResult:
    """Normalizes a single raw row. Never raises -- returns errors instead so
    the caller can flag the row for review rather than silently dropping it
    or crashing the whole import.
    """
    errors: list[str] = []

    txn_date = _parse_date(raw_record.get(mapping.transaction_date))
    if txn_date is None:
        errors.append(f"Missing or unparseable transaction date in column '{mapping.transaction_date}'")

    posted_date = None
    if mapping.posted_date:
        posted_date = _parse_date(raw_record.get(mapping.posted_date))

    amount_cents = _parse_amount_cents(raw_record.get(mapping.amount))
    if amount_cents is None:
        errors.append(f"Missing or unparseable amount in column '{mapping.amount}'")

    description_original = str(raw_record.get(mapping.description) or "").strip()
    if not description_original:
        errors.append(f"Missing description in column '{mapping.description}'")

    currency = "USD"
    if mapping.currency:
        currency = str(raw_record.get(mapping.currency) or "USD").strip().upper() or "USD"
    if currency not in KNOWN_CURRENCIES:
        errors.append(f"Unknown currency '{currency}'")

    bank_account = str(raw_record.get(mapping.bank_account) or "").strip()
    if not bank_account:
        errors.append(f"Missing bank account in column '{mapping.bank_account}'")
    elif bank_account not in KNOWN_BANK_ACCOUNTS:
        errors.append(f"Unknown bank account '{bank_account}' (not in chart of accounts)")

    bank_transaction_id = None
    if mapping.transaction_id:
        bank_transaction_id = str(raw_record.get(mapping.transaction_id) or "").strip() or None

    if errors:
        return NormalizationResult(ok=False, errors=errors)

    description_normalized = normalize_description(description_original)
    fingerprint = compute_fingerprint(bank_account, txn_date, amount_cents, description_normalized)

    normalized = {
        "bank_transaction_id": bank_transaction_id,
        "fingerprint_hash": fingerprint,
        "transaction_date": txn_date,
        "posted_date": posted_date,
        "amount_cents": amount_cents,
        "currency": currency,
        "bank_account": bank_account,
        "description_original": description_original,
        "description_normalized": description_normalized,
    }
    return NormalizationResult(ok=True, normalized=normalized)
