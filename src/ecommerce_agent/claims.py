"""Validate a deliberately small claim language and render its factual statements.

This is not arbitrary natural-language semantic verification. The model chooses
the scope and type; all wording and numbers below come from computed metrics.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["metric_comparison", "decline_source", "accounting_bridge"] = Field(
        description="metric_comparison 比较两期指标；decline_source 核对最大下降分组；accounting_bridge 核对退款前销售额变化减退款变化。不要提交自由文本。")
    scope: Literal["overall", "channel", "category"] = "overall"
    group: str | None = Field(default=None, max_length=100, description="channel/category 必须填写实际分组名；overall 不填写")
    metric: Literal["net_sales", "order_count", "aov", "refund_rate", "pre_refund_sales", "refund_amount", "quantity"] = "net_sales"
    evidence_ids: list[str] = Field(min_length=1, max_length=10, description="必须包含 metrics；可附已登记的 SQL 证据 ID")

    @model_validator(mode="after")
    def check_scope(self):
        if self.scope == "overall" and self.group is not None:
            raise ValueError("overall 不接受 group")
        if self.scope != "overall" and not self.group:
            raise ValueError("channel/category 必须指定 group")
        if self.kind == "decline_source" and self.scope == "overall":
            raise ValueError("decline_source 必须选择 channel 或 category")
        if self.kind != "metric_comparison" and self.metric != "net_sales":
            raise ValueError("下降来源及账面桥接只适用于净销售额")
        return self


LABELS = {"net_sales": "净销售额", "order_count": "订单量", "aov": "净客单价", "refund_rate": "金额退款率",
          "pre_refund_sales": "退款前销售额", "refund_amount": "退款金额", "quantity": "商品数量"}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("指标不可计算，不能形成该结论")
    return value


def _format(value, metric="net_sales"):
    if metric == "refund_rate":
        return f"{value:.2%}"
    if metric in {"order_count", "quantity"}:
        return f"{value:,.0f}" + (" 单" if metric == "order_count" else " 件")
    return f"{value:,.2f} 元"


def _pair(claim, metrics):
    if claim.scope == "overall":
        return metrics["overall"], "overall", "总体"
    section = "by_" + claim.scope
    if claim.group not in metrics.get(section, {}):
        raise ValueError(f"分组不存在或该维度不可用：{claim.group}")
    return metrics[section][claim.group], f"{section}.{claim.group}", f"{claim.group}{'渠道' if claim.scope == 'channel' else '品类'}"


def canonical_statement(claim: Claim, metrics: dict) -> tuple[str, list[str]]:
    """Return verified wording and precise metric paths, or refuse the claim."""
    pair, path, label = _pair(claim, metrics)
    if claim.kind == "metric_comparison":
        a = _number(pair["control"].get(claim.metric))
        b = _number(pair["analysis"].get(claim.metric))
        delta = b - a
        change = f"{delta * 100:+.2f} 个百分点" if claim.metric == "refund_rate" else _format(abs(delta), claim.metric)
        direction = "增加" if delta > 0 else "减少" if delta < 0 else "不变，差额为"
        if claim.metric == "refund_rate":
            direction = "变化"
        return (f"{label}{LABELS[claim.metric]}由 {_format(a, claim.metric)} 变为 {_format(b, claim.metric)}，{direction} {change}。",
                [f"{path}.control.{claim.metric}", f"{path}.analysis.{claim.metric}"])
    if claim.kind == "decline_source":
        total = _number(metrics["overall"].get("difference"))
        if total >= 0:
            raise ValueError("总体没有下降，不接受下降来源结论")
        groups = metrics["by_" + claim.scope]
        deltas = {k: _number(v.get("difference")) for k, v in groups.items()}
        minimum = min(deltas.values())
        delta = deltas[claim.group]
        if delta >= 0 or abs(delta - minimum) > 0.000001:
            raise ValueError("指定分组不是最大下降分组")
        contribution = _number(pair.get("contribution"))
        if abs(contribution - delta / total) > 0.000001:
            raise ValueError("下降贡献与分组差额/总体差额不一致")
        tied = [k for k, value in deltas.items() if abs(value - minimum) <= 0.000001]
        largest = "并列最大下降" if len(tied) > 1 else "最大下降"
        return (f"{label}净销售额减少 {_format(-delta)}，占总体净销售额减少额的 {contribution:.2%}，为{largest}{'渠道' if claim.scope == 'channel' else '品类'}。",
                [f"{path}.difference", f"{path}.contribution", "overall.difference"])
    a, b = pair["control"], pair["analysis"]
    pre = _number(b.get("pre_refund_sales")) - _number(a.get("pre_refund_sales"))
    refund = _number(b.get("refund_amount")) - _number(a.get("refund_amount"))
    net = _number(b.get("net_sales")) - _number(a.get("net_sales"))
    if abs(pre - refund - net) > 0.01000001:
        raise ValueError("退款账面桥接不守恒")
    return (f"{label}退款前销售额变化为 {pre:+,.2f} 元，退款金额变化为 {refund:+,.2f} 元；按净销售额定义，前者减后者等于净销售额变化 {net:+,.2f} 元。这是账面恒等关系，不能说明退款变化的业务原因。",
            [f"{path}.{period}.{key}" for period in ("control", "analysis") for key in ("pre_refund_sales", "refund_amount", "net_sales")])


def validate_findings(findings: list[dict], metrics: dict, evidence: dict) -> dict:
    """Check typed claims; do not certify model-written prose or legacy findings."""
    checks, errors, verified = [], [], []
    if not findings:
        errors.append("至少提交一项结构化结论")
    for index, raw in enumerate(findings):
        try:
            claim = raw if isinstance(raw, Claim) else Claim.model_validate(raw)
            if "metrics" not in claim.evidence_ids:
                raise ValueError("结论必须引用 metrics")
            missing = set(claim.evidence_ids) - evidence.keys()
            if missing:
                raise ValueError(f"证据 ID 不存在：{sorted(missing)}")
            statement, paths = canonical_statement(claim, metrics)
            verified.append({"claim": claim.model_dump(), "statement": statement, "evidence_ids": claim.evidence_ids,
                             "metric_paths": paths, "verification": "typed_metric_check"})
            checks.append({"index": index, "passed": True, "metric_paths": paths})
        except (ValueError, KeyError, TypeError) as exc:
            message = str(exc)
            checks.append({"index": index, "passed": False, "reason": message})
            errors.append(f"结论 {index + 1}: {message}")
    return {"passed": not errors, "checks": checks, "errors": errors,
            "warnings": ["仅验证受支持的结构化指标结论，不验证任意自然语言，也不进行业务因果识别。"],
            "findings": verified}
