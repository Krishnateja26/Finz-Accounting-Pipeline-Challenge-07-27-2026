from app.services.qbo_client import build_request_id, parse_pnl_report_to_account_totals


def test_request_id_is_stable_for_same_transaction():
    id1 = build_request_id("realm-1", "txn-abc")
    id2 = build_request_id("realm-1", "txn-abc")
    assert id1 == id2


def test_request_id_differs_for_different_transactions():
    id1 = build_request_id("realm-1", "txn-abc")
    id2 = build_request_id("realm-1", "txn-xyz")
    assert id1 != id2


def test_request_id_differs_for_different_realms():
    id1 = build_request_id("realm-1", "txn-abc")
    id2 = build_request_id("realm-2", "txn-abc")
    assert id1 != id2


def test_parse_nested_pnl_report():
    report = {
        "Rows": {"Row": [
            {"type": "Section", "Rows": {"Row": [
                {"type": "Data", "ColData": [{"value": "Repair Service Revenue"}, {"value": "1314.50"}]},
                {"type": "Data", "ColData": [{"value": "Installation Revenue"}, {"value": "1629.00"}]},
            ]}},
            {"type": "Data", "ColData": [{"value": "Net Income"}, {"value": "681.80"}]},
        ]},
    }
    totals = parse_pnl_report_to_account_totals(report)
    assert totals["Repair Service Revenue"] == 131450
    assert totals["Installation Revenue"] == 162900
    assert totals["Net Income"] == 68180
