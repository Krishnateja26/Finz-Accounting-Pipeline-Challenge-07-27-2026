from app.services.normalization import ColumnMapping, compute_fingerprint, normalize_row


def test_valid_row_normalizes(default_mapping):
    row = {
        "Bank Transaction ID": "BF-1", "Transaction Date": "2026-04-01T00:00:00",
        "Posted Date": "2026-04-01T00:00:00", "Description": "ACH  CREDIT   TEST  CO",
        "Amount (USD)": -100.5, "Currency": "USD", "Bank Account": "Operating Checking",
    }
    result = normalize_row(row, default_mapping)
    assert result.ok
    assert result.normalized["amount_cents"] == -10050
    assert result.normalized["description_normalized"] == "ACH CREDIT TEST CO"


def test_missing_date_is_flagged(default_mapping):
    row = {"Bank Transaction ID": "X", "Transaction Date": "", "Posted Date": "", "Description": "Y",
           "Amount (USD)": 10, "Currency": "USD", "Bank Account": "Operating Checking"}
    result = normalize_row(row, default_mapping)
    assert not result.ok
    assert any("date" in e.lower() for e in result.errors)


def test_invalid_amount_is_flagged(default_mapping):
    row = {"Bank Transaction ID": "X", "Transaction Date": "2026-04-01", "Posted Date": "",
           "Description": "Y", "Amount (USD)": "not-a-number", "Currency": "USD", "Bank Account": "Operating Checking"}
    result = normalize_row(row, default_mapping)
    assert not result.ok
    assert any("amount" in e.lower() for e in result.errors)


def test_unknown_currency_is_flagged(default_mapping):
    row = {"Bank Transaction ID": "X", "Transaction Date": "2026-04-01", "Posted Date": "",
           "Description": "Y", "Amount (USD)": 10, "Currency": "GBP", "Bank Account": "Operating Checking"}
    result = normalize_row(row, default_mapping)
    assert not result.ok
    assert any("currency" in e.lower() for e in result.errors)


def test_unknown_bank_account_is_flagged(default_mapping):
    row = {"Bank Transaction ID": "X", "Transaction Date": "2026-04-01", "Posted Date": "",
           "Description": "Y", "Amount (USD)": 10, "Currency": "USD", "Bank Account": "Some Other Bank"}
    result = normalize_row(row, default_mapping)
    assert not result.ok
    assert any("bank account" in e.lower() for e in result.errors)


def test_missing_transaction_id_does_not_error(default_mapping):
    """A missing transaction ID is not fatal -- it falls back to fingerprint dedup."""
    row = {"Bank Transaction ID": "", "Transaction Date": "2026-04-01", "Posted Date": "",
           "Description": "Y", "Amount (USD)": 10, "Currency": "USD", "Bank Account": "Operating Checking"}
    result = normalize_row(row, default_mapping)
    assert result.ok
    assert result.normalized["bank_transaction_id"] is None
    assert result.normalized["fingerprint_hash"]


def test_accounting_style_negative_amount_parses(default_mapping):
    row = {"Bank Transaction ID": "X", "Transaction Date": "2026-04-01", "Posted Date": "",
           "Description": "Y", "Amount (USD)": "(1,234.56)", "Currency": "USD", "Bank Account": "Operating Checking"}
    result = normalize_row(row, default_mapping)
    assert result.ok
    assert result.normalized["amount_cents"] == -123456


def test_fingerprint_is_stable_and_order_sensitive():
    import datetime
    f1 = compute_fingerprint("Operating Checking", datetime.date(2026, 4, 1), -100, "Test Co")
    f2 = compute_fingerprint("Operating Checking", datetime.date(2026, 4, 1), -100, "Test Co")
    f3 = compute_fingerprint("Operating Checking", datetime.date(2026, 4, 2), -100, "Test Co")
    assert f1 == f2
    assert f1 != f3


def test_column_mapping_auto_suggestion():
    mapping = ColumnMapping.suggest(["Bank Transaction ID", "Transaction Date", "Posted Date",
                                      "Description", "Amount (USD)", "Currency", "Bank Account"])
    assert mapping.transaction_id == "Bank Transaction ID"
    assert mapping.amount == "Amount (USD)"


def test_column_mapping_works_with_reordered_or_renamed_columns():
    """A totally different bank's export -- columns in a different order and
    with different names -- should still map sensibly via suggest()."""
    mapping = ColumnMapping.suggest(["Account", "Amount", "Memo", "Date", "Reference"])
    assert mapping.bank_account == "Account"
    assert mapping.amount == "Amount"
    assert mapping.description == "Memo"
    assert mapping.transaction_date == "Date"
