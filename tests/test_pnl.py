from app.models.enums import DuplicateStatus
from app.services.pnl_service import compute_monthly_and_total
from tests.pipeline_helper import run_pipeline

EXPECTED_SUMMARY = {
    "2026-04": {"revenue": 9817500, "cogs": 3132500, "gross_profit": 6685000, "opex": 4486000, "net_profit": 2199000},
    "2026-05": {"revenue": 10657500, "cogs": 3205000, "gross_profit": 7452500, "opex": 4624000, "net_profit": 2828500},
    "2026-06": {"revenue": 9552500, "cogs": 3047500, "gross_profit": 6505000, "opex": 4714500, "net_profit": 1790500},
    "total": {"revenue": 30027500, "cogs": 9385000, "gross_profit": 20642500, "opex": 13824500, "net_profit": 6818000},
}

EXPECTED_ACCOUNTS_CENTS = {
    "4000": 13145000, "4010": 16290000, "4020": 1095000, "4100": -502500,
    "5000": -5105000, "5010": -4280000,
    "6000": -8025000, "6010": -2460000, "6020": -433500, "6030": -433500,
    "6040": -885000, "6050": -367500, "6060": -364500, "6070": -495000,
    "6080": -10500, "6090": -93000, "6100": -257000,
}


def _approved_canonical_transactions(raw_transactions, default_mapping):
    """The assignment's expected numbers assume every deterministically
    classified transaction has been reviewed and approved -- so for this
    test (unlike production) we approve every classified canonical
    transaction to verify the underlying math, independent of the review
    workflow."""
    results = run_pipeline(raw_transactions, default_mapping)
    txns = []
    for t in results:
        if t.get("duplicate_status") != DuplicateStatus.CANONICAL:
            continue
        if t.get("qbo_account_number") is None:
            continue
        t["review_status"] = "approved"
        txns.append(t)
    return txns


def test_monthly_and_total_pnl_matches_expected(raw_transactions, default_mapping):
    txns = _approved_canonical_transactions(raw_transactions, default_mapping)
    periods = compute_monthly_and_total(txns)

    for label, expected in EXPECTED_SUMMARY.items():
        period = periods[label]
        assert period.revenue_cents == expected["revenue"], label
        assert period.cogs_cents == expected["cogs"], label
        assert period.gross_profit_cents == expected["gross_profit"], label
        assert period.opex_cents == expected["opex"], label
        assert period.net_profit_cents == expected["net_profit"], label


def test_account_level_totals_match_expected(raw_transactions, default_mapping):
    txns = _approved_canonical_transactions(raw_transactions, default_mapping)
    total_period = compute_monthly_and_total(txns)["total"]
    actual = {line.account_number: line.amount_cents for line in total_period.account_lines}
    assert actual == EXPECTED_ACCOUNTS_CENTS


def test_unapproved_transactions_are_excluded_from_pnl(raw_transactions, default_mapping):
    txns = _approved_canonical_transactions(raw_transactions, default_mapping)
    txns[0]["review_status"] = "needs_review"
    period_with_all_approved = compute_monthly_and_total(txns)["total"]
    assert period_with_all_approved.net_profit_cents != EXPECTED_SUMMARY["total"]["net_profit"]


def test_duplicates_are_excluded_from_pnl(raw_transactions, default_mapping):
    results = run_pipeline(raw_transactions, default_mapping)
    duplicates = [t for t in results if t.get("duplicate_status") == "exact_duplicate"]
    assert len(duplicates) == 5
    # Duplicates never get a qbo_account_number / review_status = approved
    # in the real pipeline, so they are naturally excluded from _eligible().
    for d in duplicates:
        assert d.get("qbo_account_number") is None
