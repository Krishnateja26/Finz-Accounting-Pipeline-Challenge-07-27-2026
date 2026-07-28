from app.models.enums import DuplicateStatus
from tests.pipeline_helper import run_pipeline


def test_all_canonical_transactions_are_classified(raw_transactions, default_mapping):
    results = run_pipeline(raw_transactions, default_mapping)
    canonical = [t for t in results if t.get("duplicate_status") == DuplicateStatus.CANONICAL]
    # Transfers are intentionally classified with transaction_type=transfer
    # but no P&L account_number, since they never appear on the P&L.
    unclassified = [t for t in canonical if t.get("transaction_type") is None]
    assert unclassified == [], f"Unclassified: {[t['description_original'] for t in unclassified]}"


def test_installation_receipts_are_not_classified_as_repair(raw_transactions, default_mapping):
    results = run_pipeline(raw_transactions, default_mapping)
    for t in results:
        if t.get("duplicate_status") != DuplicateStatus.CANONICAL:
            continue
        desc = t["description_normalized"].upper()
        if "INSTALL" in desc or "PROJECT" in desc:
            if t["amount_cents"] > 0:  # a customer receipt, not a subcontractor payment
                assert t["qbo_account_number"] == "4010", t["description_original"]


def test_subcontractor_payments_are_cogs_not_installation_revenue(raw_transactions, default_mapping):
    results = run_pipeline(raw_transactions, default_mapping)
    for t in results:
        if t.get("duplicate_status") != DuplicateStatus.CANONICAL:
            continue
        if "PRECISION INSTALL SVCS" in t["description_normalized"].upper():
            assert t["qbo_account_number"] == "5010"
            assert t["amount_cents"] < 0


def test_equipment_purchase_is_fixed_asset_not_expense(raw_transactions, default_mapping):
    results = run_pipeline(raw_transactions, default_mapping)
    matches = [t for t in results if "MILWAUKEE COMMERCIAL TOOL PACKAGE" in t["description_normalized"].upper()]
    assert len(matches) == 1
    assert matches[0]["transaction_type"] == "fixed_asset_purchase"
    assert matches[0]["qbo_account_number"] == "1500"


def test_owner_activity_is_classified_correctly(raw_transactions, default_mapping):
    results = run_pipeline(raw_transactions, default_mapping)
    contribution = [t for t in results if "OWNER CAPITAL" in t["description_normalized"].upper()]
    distribution = [t for t in results if "OWNER DISTRIBUTION" in t["description_normalized"].upper()]
    assert len(contribution) == 1 and contribution[0]["transaction_type"] == "owner_contribution"
    assert len(distribution) == 1 and distribution[0]["transaction_type"] == "owner_distribution"


def test_internal_transfers_are_not_revenue_or_expense(raw_transactions, default_mapping):
    results = run_pipeline(raw_transactions, default_mapping)
    transfers = [t for t in results if "TAX RESERVE TRANSFER" in t["description_normalized"].upper()]
    assert len(transfers) == 12
    assert all(t["transaction_type"] == "transfer" for t in transfers)
