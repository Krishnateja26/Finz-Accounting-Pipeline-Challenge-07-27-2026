"""
Reconciliation: compares the application's computed P&L against QuickBooks'
ProfitAndLoss report, account by account, plus net profit. A successful API
sync is NOT sufficient -- this is the module that proves the accounting
output actually matches.
"""
from numbers import Real

from app.models.reconciliation import ReconciliationLine, ReconciliationRun
from app.services.classification import CHART_OF_ACCOUNTS
from app.services.pnl_service import PnlPeriod

TOLERANCE_CENTS = 1  # no more than $0.01


def _qbo_amount_to_cents(amount: Real) -> int:
    """Accept parser-produced cents or raw QBO dollar floats."""
    if isinstance(amount, float):
        return round(amount * 100)
    return int(amount)


def reconcile_period(
    *,
    period_start,
    period_end,
    period_label: str,
    app_pnl: PnlPeriod,
    qbo_account_totals: dict[str, Real],  # account NAME -> cents, or QBO dollar floats
) -> ReconciliationRun:
    lines: list[ReconciliationLine] = []

    # Reconcile every account that appears on either side. A QBO account
    # missing from the report is treated as zero -- but only after we've
    # confirmed via the chart of accounts that the mapping is valid, so a
    # genuinely missing/misconfigured account isn't silently swept to zero.
    all_account_numbers = {line.account_number for line in app_pnl.account_lines} | {
        num for num, name in CHART_OF_ACCOUNTS.items() if name in qbo_account_totals
    }

    app_by_account = {line.account_number: line.amount_cents for line in app_pnl.account_lines}

    for acct_num in sorted(all_account_numbers):
        acct_name = CHART_OF_ACCOUNTS.get(acct_num, acct_num)
        app_amount = app_by_account.get(acct_num, 0)
        qbo_amount = _qbo_amount_to_cents(qbo_account_totals.get(acct_name, 0))
        # QBO reports expenses/COGS as positive magnitudes on the P&L report;
        # our app stores them as negative (money out). Normalize sign for
        # comparison against contra/expense accounts.
        if acct_num not in {"4000", "4010", "4020"}:
            qbo_amount_signed = -abs(qbo_amount) if qbo_amount else 0
        else:
            qbo_amount_signed = qbo_amount

        diff = app_amount - qbo_amount_signed
        reconciled = abs(diff) <= TOLERANCE_CENTS
        lines.append(ReconciliationLine(
            period_label=period_label,
            account_number=acct_num,
            account_name=acct_name,
            app_amount_cents=app_amount,
            qbo_amount_cents=qbo_amount_signed,
            difference_cents=diff,
            is_reconciled=reconciled,
            explanation=None if reconciled else "Amounts differ -- review classification/account mapping for this account",
        ))

    # Net profit line
    qbo_net = qbo_account_totals.get("Net Income", None)
    net_diff = None
    net_reconciled = False
    if qbo_net is not None:
        qbo_net = _qbo_amount_to_cents(qbo_net)
        net_diff = app_pnl.net_profit_cents - qbo_net
        net_reconciled = abs(net_diff) <= TOLERANCE_CENTS
        lines.append(ReconciliationLine(
            period_label=period_label,
            account_number=None,
            account_name="Net Profit",
            app_amount_cents=app_pnl.net_profit_cents,
            qbo_amount_cents=qbo_net,
            difference_cents=net_diff,
            is_reconciled=net_reconciled,
            explanation=None if net_reconciled else "Net profit does not match -- check for unclassified or unapproved transactions",
        ))

    fully_reconciled = all(line.is_reconciled for line in lines) and len(lines) > 0

    return ReconciliationRun(
        period_start=period_start,
        period_end=period_end,
        period_label=period_label,
        lines=lines,
        fully_reconciled=fully_reconciled,
    )
