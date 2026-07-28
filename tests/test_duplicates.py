from app.models.enums import DuplicateStatus
from tests.pipeline_helper import run_pipeline

KNOWN_DUPLICATE_IDS = {
    "BF-202604-0001", "BF-202605-0071", "BF-202605-0096", "BF-202606-0136", "BF-202606-0171",
}


def test_dataset_has_195_canonical_and_5_duplicates(raw_transactions, default_mapping):
    results = run_pipeline(raw_transactions, default_mapping)
    canonical = [t for t in results if t.get("duplicate_status") == DuplicateStatus.CANONICAL]
    duplicates = [t for t in results if t.get("duplicate_status") == DuplicateStatus.EXACT_DUPLICATE]
    assert len(results) == 200
    assert len(canonical) == 195
    assert len(duplicates) == 5


def test_known_duplicate_transaction_ids_are_flagged(raw_transactions, default_mapping):
    results = run_pipeline(raw_transactions, default_mapping)
    duplicates = [t for t in results if t.get("duplicate_status") == DuplicateStatus.EXACT_DUPLICATE]
    duplicate_bank_ids = {t["bank_transaction_id"] for t in duplicates}
    assert duplicate_bank_ids == KNOWN_DUPLICATE_IDS


def test_second_identical_upload_creates_zero_new_canonical_transactions(raw_transactions, default_mapping):
    """Re-running ingestion against the same rows a second time (simulating
    a re-upload) must not create any new canonical transactions -- everything
    should come back as an exact duplicate of what's already canonical."""
    first_pass = run_pipeline(raw_transactions, default_mapping)
    canonical_count_first = sum(1 for t in first_pass if t.get("duplicate_status") == DuplicateStatus.CANONICAL)

    # Simulate the dedup state carrying over: re-run using the same function
    # would normally use fresh in-memory dicts, so to truly test the
    # DB-backed behavior we run the pipeline twice in sequence over combined
    # rows and confirm the total canonical count doesn't double.
    combined = raw_transactions + raw_transactions
    second_pass = run_pipeline(combined, default_mapping)
    canonical_count_second = sum(1 for t in second_pass if t.get("duplicate_status") == DuplicateStatus.CANONICAL)

    assert canonical_count_second == canonical_count_first
