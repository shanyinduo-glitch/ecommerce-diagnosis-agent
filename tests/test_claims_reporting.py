import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from ecommerce_agent.claims import Claim, canonical_statement, validate_findings
from ecommerce_agent.reporting import charts, report


@pytest.fixture
def metrics():
    a = {"net_sales": 95, "order_count": 5, "aov": 19, "refund_rate": .05, "pre_refund_sales": 100, "refund_amount": 5, "quantity": 6}
    b = {"net_sales": 85, "order_count": 5, "aov": 17, "refund_rate": 25 / 110, "pre_refund_sales": 110, "refund_amount": 25, "quantity": 6}
    pair = {"control": a, "analysis": b, "difference": -10, "change_rate": -10 / 95}
    group = {**deepcopy(pair), "control_net_sales": 95, "analysis_net_sales": 85, "contribution": 1}
    return {"overall": pair, "by_channel": {"搜索": deepcopy(group)}, "by_category": {"数码": deepcopy(group)}, "traffic_by_channel": {}}


def claim(**kwargs):
    return {"kind": "metric_comparison", "scope": "overall", "metric": "net_sales", "evidence_ids": ["metrics"], **kwargs}


def test_typed_claims_render_actual_values_and_precise_paths(metrics):
    checked = validate_findings([claim()], metrics, {"metrics": {}})
    assert checked["passed"]
    finding = checked["findings"][0]
    assert "95.00" in finding["statement"] and "85.00" in finding["statement"]
    assert finding["metric_paths"] == ["overall.control.net_sales", "overall.analysis.net_sales"]


def test_free_text_cannot_be_certified(metrics):
    for text in ("竞争对手促销导致下降", "高价值服饰成交减少", "退款和净客单价分别导致20元损失"):
        checked = validate_findings([claim(statement=text)], metrics, {"metrics": {}})
        assert not checked["passed"] and not checked["findings"]
    assert not validate_findings([{"statement": "净额下降", "evidence_ids": ["metrics"]}], metrics, {"metrics": {}})["passed"]


def test_refund_bridge_offsets_growth_without_double_counting(metrics):
    checked = validate_findings([claim(kind="accounting_bridge")], metrics, {"metrics": {}})
    assert checked["passed"]
    text = checked["findings"][0]["statement"]
    assert "+10.00" in text and "+20.00" in text and "-10.00" in text
    assert "不能说明" in text
    metrics["overall"]["analysis"]["net_sales"] = 80
    assert not validate_findings([claim(kind="accounting_bridge")], metrics, {"metrics": {}})["passed"]


def test_decline_claim_checks_rank_ties_and_contribution(metrics):
    source = claim(kind="decline_source", scope="channel", group="搜索")
    assert validate_findings([source], metrics, {"metrics": {}})["passed"]
    metrics["by_channel"]["App"] = deepcopy(metrics["by_channel"]["搜索"])
    assert "并列" in validate_findings([source], metrics, {"metrics": {}})["findings"][0]["statement"]
    metrics["by_channel"]["App"]["difference"] = -20
    assert not validate_findings([source], metrics, {"metrics": {}})["passed"]
    del metrics["by_channel"]["App"]
    metrics["by_channel"]["搜索"]["contribution"] = .5
    assert not validate_findings([source], metrics, {"metrics": {}})["passed"]


def test_no_decline_null_and_invalid_evidence_refused(metrics):
    source = claim(kind="decline_source", scope="channel", group="搜索")
    metrics["overall"]["difference"] = 0
    assert not validate_findings([source], metrics, {"metrics": {}})["passed"]
    metrics["overall"]["control"]["net_sales"] = None
    assert not validate_findings([claim()], metrics, {"metrics": {}})["passed"]
    assert validate_findings([claim(metric="order_count")], metrics, {"metrics": {}})["passed"]
    assert not validate_findings([claim(metric="order_count", evidence_ids=["made_up"])], metrics, {"metrics": {}})["passed"]
    assert not validate_findings([], metrics, {"metrics": {}})["passed"]


def test_schema_rejects_ambiguous_scope_and_non_net_bridge():
    with pytest.raises(ValidationError):
        Claim(**claim(scope="channel"))
    with pytest.raises(ValidationError):
        Claim(**claim(group="搜索"))
    with pytest.raises(ValidationError):
        Claim(**claim(kind="accounting_bridge", metric="aov"))


def test_report_regenerates_verified_text_and_labels_legacy(metrics, tmp_path):
    verified = validate_findings([claim()], metrics, {"metrics": {}})["findings"][0]
    verified["statement"] = "竞争对手促销已被证明"
    report(tmp_path, metrics, None, [verified, {"statement": "历史解释", "evidence_ids": ["metrics"]}], "completed", [],
           {"control_start": "2026-09-01", "control_end": "2026-09-14", "analysis_start": "2026-09-15", "analysis_end": "2026-09-28"}, {})
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "竞争对手促销已被证明" not in text
    assert "95.00" in text and "历史模型原文，未经语义验证" in text


def test_partial_report_and_charts_do_not_turn_unknown_into_zero(tmp_path):
    metrics = {"overall": {"control": {"order_count": 3}, "analysis": {"order_count": 2}, "difference": None},
               "by_channel": {}, "by_category": {}, "traffic_by_channel": {},
               "availability": {"full": False, "reasons": ["refund_amount 缺失，净销售额不可计算"]}}
    config = {"control_start": "2026-09-01", "control_end": "2026-09-14", "analysis_start": "2026-09-15", "analysis_end": "2026-09-28"}
    report(tmp_path, metrics, None, [], "partial", [], config, {})
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "不判断总体是否下降" in text and "refund_amount 缺失" in text
    files = charts(metrics, tmp_path, config)
    index = json.loads((tmp_path / "figure_index.json").read_text(encoding="utf-8"))
    assert len(files) == 3 and all(not x["available"] for x in index["figures"])
    assert all((tmp_path / x["svg"]).exists() for x in index["figures"])


def test_negative_bridge_labels_stay_inside_axes(tmp_path, monkeypatch):
    from matplotlib.figure import Figure
    original = Figure.savefig
    inspected = []
    def save(figure, path, *args, **kwargs):
        if str(path).endswith("refund_bridge.png"):
            figure.canvas.draw()
            renderer = figure.canvas.get_renderer()
            ax = figure.axes[0]
            limits = ax.get_window_extent(renderer)
            for text in ax.texts:
                bounds = text.get_window_extent(renderer)
                assert bounds.y0 >= limits.y0 and bounds.y1 <= limits.y1
            inspected.append(True)
        return original(figure, path, *args, **kwargs)
    monkeypatch.setattr(Figure, "savefig", save)
    metrics = {"overall": {"decomposition": {"refund_bridge": {
        "available": True, "pre_refund_change": 21123.29, "refund_effect": -253565.41,
        "net_change": -232442.12}}}, "by_channel": {}, "by_category": {}}
    config = {"control_start": "2026-09-01", "control_end": "2026-09-14", "analysis_start": "2026-09-15", "analysis_end": "2026-09-28"}
    charts(metrics, tmp_path, config)
    assert inspected
