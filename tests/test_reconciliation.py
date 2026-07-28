from datetime import date

from app.services.pnl_service import AccountLine, PnlPeriod
from app.services.reconciliation_service import reconcile_period


def _make_app_pnl():
    return PnlPeriod(
        label="2026-04",
        revenue_cents=9817500,
        cogs_cents=3132500,
        gross_profit_cents=6685000,
        opex_cents=4486000,
        net_profit_cents=2199000,
        account_lines=[
            AccountLine("4000", "Repair Service Revenue", 4277500),
            AccountLine("5000", "Materials & Supplies", -1502500),
        ],
    )


def test_matching_totals_are_fully_reconciled():
    app_pnl = _make_app_pnl()
    qbo_totals = {
        "Repair Service Revenue": 42775.0,  # QBO reports in dollars, positive
        "Materials & Supplies": 15025.0,    # QBO reports expense magnitude as positive
        "Net Income": 21990.0,
    }
    run = reconcile_period(
        period_start=date(2026, 4, 1), period_end=date(2026, 4, 30), period_label="2026-04",
        app_pnl=app_pnl, qbo_account_totals=qbo_totals,
    )
    assert run.fully_reconciled
    assert all(line.is_reconciled for line in run.lines)


def test_mismatched_totals_are_flagged():
    app_pnl = _make_app_pnl()
    qbo_totals = {
        "Repair Service Revenue": 40000.0,  # wrong on purpose
        "Materials & Supplies": 15025.0,
        "Net Income": 21990.0,
    }
    run = reconcile_period(
        period_start=date(2026, 4, 1), period_end=date(2026, 4, 30), period_label="2026-04",
        app_pnl=app_pnl, qbo_account_totals=qbo_totals,
    )
    assert not run.fully_reconciled
    revenue_line = next(l for l in run.lines if l.account_number == "4000")
    assert not revenue_line.is_reconciled
    assert revenue_line.difference_cents != 0


def test_tolerance_allows_rounding_of_one_cent():
    app_pnl = _make_app_pnl()
    qbo_totals = {
        "Repair Service Revenue": 42775.01,  # one cent off due to rounding
        "Materials & Supplies": 15025.0,
        "Net Income": 21990.0,
    }
    run = reconcile_period(
        period_start=date(2026, 4, 1), period_end=date(2026, 4, 30), period_label="2026-04",
        app_pnl=app_pnl, qbo_account_totals=qbo_totals,
    )
    revenue_line = next(l for l in run.lines if l.account_number == "4000")
    assert revenue_line.is_reconciled
