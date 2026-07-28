"""
Cash-basis P&L computation.

Takes a list of already-normalized, already-classified, already-approved
transaction dicts and produces a P&L. This function does not touch Mongo --
the API layer is responsible for querying only:
    duplicate_status == canonical
    review_status == approved
    transaction_type not in NON_PNL_TYPES
and passing the result in here. Keeping this pure makes it trivial to unit
test against the assignment's known-correct totals.

Recognition uses `transaction_date` (not `posted_date`), matching the
assignment's stated cash-basis recognition rule.
"""
from collections import defaultdict
from dataclasses import dataclass, field

from app.models.enums import NON_PNL_TYPES, TransactionType
from app.services.classification import CHART_OF_ACCOUNTS

REVENUE_ACCOUNTS = {"4000", "4010", "4020", "4100"}  # 4100 Customer Refunds is contra-revenue
COGS_ACCOUNTS = {"5000", "5010"}
# Everything else in the chart of accounts that is an Expense-type account
OPEX_ACCOUNTS = {
    num for num in CHART_OF_ACCOUNTS
    if num not in REVENUE_ACCOUNTS and num not in COGS_ACCOUNTS
    and num not in {"1000", "1010", "1500", "3000"}  # bank/asset/equity accounts, not P&L
}


@dataclass
class AccountLine:
    account_number: str
    account_name: str
    amount_cents: int
    transaction_ids: list[str] = field(default_factory=list)


@dataclass
class PnlPeriod:
    label: str
    revenue_cents: int
    cogs_cents: int
    gross_profit_cents: int
    opex_cents: int
    net_profit_cents: int
    account_lines: list[AccountLine]


def _eligible(txn: dict) -> bool:
    """A transaction counts toward the P&L only if it is the canonical
    (non-duplicate) record, has been approved for accounting purposes, and
    is a P&L-relevant type. Unapproved and error records are excluded, per
    the assignment."""
    if txn.get("duplicate_status") != "canonical":
        return False
    if txn.get("review_status") != "approved":
        return False
    ttype = txn.get("transaction_type")
    if ttype is None or ttype in NON_PNL_TYPES or ttype == TransactionType.UNKNOWN:
        return False
    if not txn.get("qbo_account_number") and not txn.get("account_number"):
        return False
    return True


def compute_pnl(transactions: list[dict], label: str) -> PnlPeriod:
    """`transactions` should already be filtered to the desired date range;
    this function only filters by eligibility, not by date."""
    by_account: dict[str, AccountLine] = {}

    for txn in transactions:
        if not _eligible(txn):
            continue
        acct = txn.get("qbo_account_number") or txn.get("account_number")
        if acct not in by_account:
            by_account[acct] = AccountLine(
                account_number=acct,
                account_name=CHART_OF_ACCOUNTS.get(acct, acct),
                amount_cents=0,
            )
        line = by_account[acct]
        line.amount_cents += txn["amount_cents"]
        line.transaction_ids.append(txn.get("_id") or txn.get("id"))

    revenue = sum(l.amount_cents for a, l in by_account.items() if a in REVENUE_ACCOUNTS)
    cogs = -sum(l.amount_cents for a, l in by_account.items() if a in COGS_ACCOUNTS)
    opex = -sum(l.amount_cents for a, l in by_account.items() if a in OPEX_ACCOUNTS)
    gross_profit = revenue - cogs
    net_profit = gross_profit - opex

    ordered_lines = sorted(
        by_account.values(),
        key=lambda l: (l.account_number not in REVENUE_ACCOUNTS, l.account_number not in COGS_ACCOUNTS, l.account_number),
    )

    return PnlPeriod(
        label=label,
        revenue_cents=revenue,
        cogs_cents=cogs,
        gross_profit_cents=gross_profit,
        opex_cents=opex,
        net_profit_cents=net_profit,
        account_lines=ordered_lines,
    )


def compute_monthly_and_total(transactions: list[dict]) -> dict[str, PnlPeriod]:
    """Buckets by `transaction_date`'s YYYY-MM, and also computes the
    combined period across everything passed in."""
    by_month: dict[str, list[dict]] = defaultdict(list)
    for txn in transactions:
        month_key = str(txn["transaction_date"])[:7]
        by_month[month_key].append(txn)

    results = {month: compute_pnl(txns, month) for month, txns in sorted(by_month.items())}
    results["total"] = compute_pnl(transactions, "total")
    return results
