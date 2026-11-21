from unittest.mock import Mock

from run_chat_cohort import capture_public_state
from score_chat_cohort import number, report_text, rows_visible, stock_slice


def test_csv_separates_channel_key_and_amount():
    report = report_text(
        "",
        [{"name": "analysis.csv", "status": 200, "text": "channel,value\r\n1,4928360\r\n7,426290"}],
    )
    assert rows_visible(report, {1: 4928360, 7: 426290}, cash=True)
    assert not number(report, 14928360)


def test_decimal_thousands_and_sign_survive_report_display():
    assert number("差额为 +1,234.50 元", 123450, money=True)
    assert number("差额为 −1，234.50 元", -123450, money=True)
    assert not number("差额为 −1,234.50 元", 123450, money=True)
    assert not number("差额为 12,345.00 元", 123450, money=True)


def test_stock_slice_recognizes_the_requested_row_in_a_filtered_group():
    by_site = {"group_by": "site", "filters": {"sku": 9}}
    assert stock_slice(by_site, "期末库存（地点1 × SKU9） | 10 | 件", 10)
    assert stock_slice({"group_by": "sku", "filters": {"site": 1}}, "SKU 9 = 10 件", 10)
    assert not stock_slice(by_site, "地点2 × SKU9 | 10 件\n地点1 × SKU9 | 4 件", 10)
    assert not stock_slice(by_site, "地点1=4、地点2=10", 10)
    assert not stock_slice(by_site, "地点1 × SKU9 | 4 件", 9)
    assert not stock_slice({"group_by": "site", "filters": {}}, "地点1 × SKU9 | 10 件", 10)


def test_expired_observation_keeps_the_original_public_run_status():
    actor = Mock()
    observed = {"status": "cancelling", "reasonCode": "cleanup_unconfirmed"}
    packets = [{"event": {"type": "run.upsert", "payload": observed}}]
    actor.messages.return_value = (observed, packets)
    sample = {"receipt": {"runId": "original"}, "terminal": "timeout"}

    assert not capture_public_state(actor, sample)
    assert sample["state"] == observed
    assert sample["packets"] == packets
    assert sample["terminal"] == "timeout"
    actor.artifacts.assert_not_called()
