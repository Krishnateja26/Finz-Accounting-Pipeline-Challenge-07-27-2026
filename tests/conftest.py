import json
from pathlib import Path

import pytest

from app.services.normalization import ColumnMapping

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="session")
def raw_transactions() -> list[dict]:
    return json.loads((DATA_DIR / "sample_raw_transactions.json").read_text())


@pytest.fixture(scope="session")
def default_mapping() -> ColumnMapping:
    return ColumnMapping(
        transaction_id="Bank Transaction ID",
        transaction_date="Transaction Date",
        posted_date="Posted Date",
        description="Description",
        amount="Amount (USD)",
        currency="Currency",
        bank_account="Bank Account",
    )
