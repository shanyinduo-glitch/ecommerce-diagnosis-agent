"""Independent small examples for arithmetic, availability, and ranking impact."""
from copy import deepcopy
import pandas as pd
import pytest
from ecommerce_agent import analysis


@pytest.fixture
def periods():
    return {"control_start": "2026-09-01", "control_end": "2026-09-01",
            "analysis_start": "2026-09-15", "analysis_end": "2026-09-15"}


def row(number, date, channel, gross, refund="0", product=None, order=None):
    return {"order_id": order or "O"+str(number), "item_id": "I"+str(number), "date": date,
            "product_id": product or "P"+channel, "channel": channel, "quantity": "1",
            "gross_amount": str(gross), "discount_amount": "0", "refund_amount": str(refund)}


@pytest.fixture
def small():
    return {"order_items": pd.DataFrame([
        row(1, "2026-09-01", "A", 100), row(2, "2026-09-15", "A", 90),
        row(3, "2026-09-01", "B", 100), row(4, "2026-09-15", "B", 80)]),
        "products": pd.DataFrame([{"product_id": "P"+x, "product_name": x, "category": "C"+x, "list_price": "100"} for x in ("A", "B")]),
        "traffic": pd.DataFrame([{"date": d, "channel": x, "visitors": "10"}
                                 for d in ("2026-09-01", "2026-09-15") for x in ("A", "B")])}


def calculate(tables, tmp_path, periods):
    items, traffic, quality = analysis.clean_available(tables, tmp_path/"prepared", periods)
    metrics = analysis.compute(items, traffic, quality, tmp_path/"tables")
    return items, traffic, quality, metrics


def test_hand_calculated_two_independent_decompositions(small, periods, tmp_path):
    # 2 orders x 90 net -> 1 order x 160 net; pre-refund +20, refund +40.
    small["order_items"] = pd.DataFrame([
        row(1, "2026-09-01", "A", 100, 10),
        row(2, "2026-09-01", "A", 100, 10),
        row(3, "2026-09-15", "A", 220, 60)])
    _, _, q, m = calculate(small, tmp_path, periods)
    assert q["availability"]["full"]
    assert m["overall"]["difference"] == -20
    bridge = m["overall"]["decomposition"]["refund_bridge"]
    assert bridge["pre_refund_change"] == 20
    assert bridge["refund_change"] == 40
    assert bridge["refund_effect"] == -40
    assert bridge["net_change"] == -20 and bridge["reconciles"]
    factors = m["overall"]["decomposition"]["order_aov"]
    assert factors["order_effect"] == -125
    assert factors["aov_effect"] == 105
    assert factors["net_change"] == -20 and factors["reconciles"]
    assert analysis.verify(m)["passed"] and analysis.verify(m)["complete"]
    assert (tmp_path/"tables/decomposition.csv").exists()


def test_duplicate_changes_top_source_and_rank(small, periods, tmp_path):
    # Raw A decline -110 versus B -20. Remove duplicate control A:
    # clean A -10 versus B -20; total raw -130 becomes clean -30.
    small["order_items"] = pd.concat([small["order_items"], small["order_items"].iloc[:1]])
    _, _, quality, metrics = calculate(small, tmp_path, periods)
    impact = quality["attribution_impact"]
    assert impact["raw_difference"] == -130
    assert impact["clean_difference"] == -30
    assert impact["difference_adjustment"] == 100
    channel = impact["dimensions"]["channel"]
    assert channel["raw_top_decline_source"] == "A"
    assert channel["clean_top_decline_source"] == "B"
    assert channel["source_changed"] and channel["rank_changed"]
    assert impact["dimensions"]["category"]["source_changed"]
    assert metrics["top_decline_source"]["channel"] == "B"
    assert (tmp_path/"attribution_impact.json").exists()


@pytest.mark.parametrize("mode", ["absent", "incomplete", "duplicate", "invalid"])
def test_bad_traffic_never_disables_unaffected_sales(small, periods, tmp_path, mode):
    if mode == "absent":
        small.pop("traffic")
    elif mode == "incomplete":
        small["traffic"] = small["traffic"].iloc[1:]
    elif mode == "duplicate":
        small["traffic"] = pd.concat([small["traffic"], small["traffic"].iloc[:1]])
    else:
        small["traffic"].loc[0, "visitors"] = "invalid"
    _, traffic, quality, metrics = calculate(small, tmp_path, periods)
    assert set(traffic.columns) >= {"date", "channel", "visitors", "period"}
    assert traffic.empty
    assert not quality["availability"]["full"]
    assert not quality["availability"]["metrics"]["traffic"]
    assert metrics["traffic_by_channel"] == {}
    assert metrics["overall"]["difference"] == -30
    assert metrics["by_channel"]["B"]["difference"] == -20
    assert analysis.verify(metrics)["passed"]
    assert not analysis.verify(metrics)["complete"]


@pytest.mark.parametrize("mode", ["absent", "duplicate", "blank_category"])
def test_invalid_product_table_disables_category_only(small, periods, tmp_path, mode):
    if mode == "absent":
        small.pop("products")
    elif mode == "duplicate":
        small["products"] = pd.concat([small["products"], small["products"].iloc[:1]])
    else:
        small["products"].loc[0, "category"] = ""
    items, _, quality, metrics = calculate(small, tmp_path, periods)
    assert metrics["overall"]["difference"] == -30
    assert metrics["by_category"] == {}
    assert "category" not in items
    assert metrics["top_decline_source"]["category"] is None
    assert quality["unmatched_product_rows"] is None
    checked = analysis.verify(metrics)
    assert "category_reconciles" in checked["skipped"]
    assert "category_reconciles" not in checked["checks"]


@pytest.mark.parametrize("mode", ["absent", "blank", "invalid"])
def test_refund_missing_does_not_become_zero(small, periods, tmp_path, mode):
    if mode == "absent":
        small["order_items"] = small["order_items"].drop(columns="refund_amount")
    else:
        small["order_items"].loc[0, "refund_amount"] = "" if mode == "blank" else "1.005"
    items, _, quality, metrics = calculate(small, tmp_path, periods)
    assert "net_cents" not in items and "refund_amount_cents" not in items
    overall = metrics["overall"]
    assert overall["difference"] is None and overall["change_rate"] is None
    assert overall["control"]["order_count"] == 2
    assert overall["analysis"]["order_count"] == 2
    assert overall["control"]["pre_refund_sales"] == 200
    assert overall["analysis"]["pre_refund_sales"] == 170
    assert overall["control"]["refund_amount"] is None
    assert overall["control"]["net_sales"] is None
    assert overall["control"]["aov"] is None
    assert overall["control"]["refund_rate"] is None
    assert not overall["decomposition"]["refund_bridge"]["available"]
    assert metrics["by_channel"]["A"]["control"]["order_count"] == 1
    assert metrics["top_decline_source"] == {"channel": None, "category": None}
    assert all(row["clean_net_sales"] is None for row in quality["bridge"])
    assert not analysis.verify(metrics)["complete"]
    assert analysis.verify(metrics)["skipped"]


def test_missing_channel_keeps_overall_and_category(small, periods, tmp_path):
    small["order_items"] = small["order_items"].drop(columns="channel")
    items, _, quality, metrics = calculate(small, tmp_path, periods)
    assert "channel" not in items
    assert metrics["overall"]["difference"] == -30
    assert metrics["by_channel"] == {}
    assert metrics["by_category"]["CB"]["difference"] == -20
    assert quality["availability"]["metrics"]["category"]


def test_invalid_channel_still_isolates_channel_only_conflicts(small, periods, tmp_path):
    conflict = small["order_items"].iloc[:1].copy()
    conflict.loc[conflict.index[0], "channel"] = "B"
    small["order_items"] = pd.concat([small["order_items"], conflict])
    _, _, quality, metrics = calculate(small, tmp_path, periods)
    assert quality["conflicting_item_ids_excluded"] == 1
    assert quality["conflict_rows_excluded"] == 2
    assert metrics["by_channel"] == {}
    assert metrics["overall"]["control"]["net_sales"] == 100
    assert metrics["overall"]["analysis"]["net_sales"] == 170


def test_unknown_category_is_distinct_from_unavailable_products(small, periods, tmp_path):
    small["order_items"].loc[0, "product_id"] = "MISSING"
    _, _, quality, metrics = calculate(small, tmp_path, periods)
    assert quality["availability"]["full"]
    assert quality["unmatched_product_rows"] == 1
    assert metrics["by_category"]["未知"]["control_net_sales"] == 100
    unknown = quality["attribution_impact"]["unknown_category"]
    assert unknown["rows"] == 1 and unknown["difference"] == -100
    assert "真实所属品类" in unknown["note"]


def test_zero_orders_does_not_invent_aov(small, periods, tmp_path):
    small["order_items"] = small["order_items"][small["order_items"].date.eq("2026-09-01")]
    _, _, _, metrics = calculate(small, tmp_path, periods)
    assert metrics["overall"]["analysis"]["order_count"] == 0
    assert metrics["overall"]["analysis"]["aov"] is None
    assert metrics["overall"]["decomposition"]["refund_bridge"]["reconciles"]
    assert not metrics["overall"]["decomposition"]["order_aov"]["available"]
    checked = analysis.verify(metrics)
    assert checked["passed"]
    assert "overall.overall.order_aov_reconciles" in checked["skipped"]


def test_tied_sources_not_presented_as_unique_in_quality_impact(small, periods, tmp_path):
    small["order_items"].loc[small["order_items"].item_id.eq("I4"), "gross_amount"] = "90"
    _, _, quality, _ = calculate(small, tmp_path, periods)
    item = quality["attribution_impact"]["dimensions"]["channel"]
    assert item["clean_top_decline_source"] is None
    assert item["clean_tied_sources"] == ["A", "B"]
    assert not item["source_changed"]


@pytest.mark.parametrize("column", ["item_id", "order_id", "date"])
def test_missing_identity_or_period_is_fatal(small, periods, tmp_path, column):
    small["order_items"] = small["order_items"].drop(columns=column)
    with pytest.raises(ValueError, match=column):
        calculate(small, tmp_path, periods)


def test_read_missing_files_can_be_inspected(tmp_path):
    tables = analysis.read_tables(tmp_path)
    info = analysis.inspect(tables)
    assert len(tables) == 3
    assert info["traffic"]["missing_columns"] == ["channel", "date", "visitors"]


def test_unavailable_quantity_keeps_money(small, periods, tmp_path):
    small["order_items"].loc[0, "quantity"] = "bad"
    _, _, _, metrics = calculate(small, tmp_path, periods)
    assert metrics["overall"]["difference"] == -30
    assert metrics["overall"]["control"]["quantity"] is None
