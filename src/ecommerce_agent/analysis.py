from __future__ import annotations
from decimal import Decimal
from pathlib import Path
import pandas as pd
from .io import dump

SCHEMA = {
    "order_items": ["order_id", "item_id", "date", "product_id", "channel", "quantity", "gross_amount", "discount_amount", "refund_amount"],
    "products": ["product_id", "product_name", "category", "list_price"],
    "traffic": ["date", "channel", "visitors"],
}


def ratio(a, b):
    return float(a / b) if a is not None and b is not None and b != 0 else None


def cents(s):
    v = Decimal(str(s)) * 100
    if not v.is_finite() or v != v.to_integral_value() or v < 0:
        raise ValueError("金额必须是非负且精确到分的数值")
    return int(v)


def read_tables(folder: Path):
    # Missing files are reported by inspect/clean_available, never fabricated.
    result = {}
    for name in SCHEMA:
        path = folder / f"{name}.csv"
        if not path.exists():
            result[name] = pd.DataFrame()
        else:
            try:
                result[name] = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
            except pd.errors.EmptyDataError:
                result[name] = pd.DataFrame()
    return result


def inspect(tables):
    return {name: {"rows": len(tables.get(name, [])),
                   "columns": list(tables.get(name, pd.DataFrame()).columns),
                   "missing_columns": sorted(set(SCHEMA[name]) - set(tables.get(name, pd.DataFrame()).columns)),
                   "blank_cells": int(tables.get(name, pd.DataFrame()).eq("").sum().sum()),
                   "sample": tables.get(name, pd.DataFrame()).head(2).to_dict("records")}
            for name in SCHEMA}


def _validate_periods(config):
    for field in ("control_start", "control_end", "analysis_start", "analysis_end"):
        pd.to_datetime(config[field], format="%Y-%m-%d", errors="raise")
    if not config["control_start"] <= config["control_end"] < config["analysis_start"] <= config["analysis_end"]:
        raise ValueError("时期必须有序且不重叠")


def _period(frame, config):
    frame = frame.copy()
    frame["period"] = "outside"
    for label in ("control", "analysis"):
        frame.loc[frame.date.between(config[label + "_start"], config[label + "_end"]), "period"] = label
    return frame


def _integers(values, minimum):
    parsed = pd.to_numeric(values, errors="raise")
    if parsed.isna().any() or ((parsed % 1 != 0) | (parsed < minimum)).any():
        raise ValueError("必须为有效整数")
    return parsed.astype("int64")


def clean(tables, out: Path, config):
    """Strict course-data contract, retained for callers requiring full input."""
    for name, info in inspect(tables).items():
        if info["missing_columns"] or info["blank_cells"]:
            raise ValueError(f"{name}: missing_columns={info['missing_columns']}, blank_cells={info['blank_cells']}; 无法补造数据，请报告受影响指标")
    return _clean(tables, out, config, strict=True)


def clean_available(tables, out: Path, config):
    """Keep unaffected metrics; never replace missing money/visits by zero.

    Period assignment and item identity remain mandatory. Missing observed
    fields also limit our ability to certify duplicate/conflict status; that
    limitation is explicitly retained in availability.reasons.
    """
    return _clean(tables, out, config, strict=False)


def _clean(tables, out, config, strict):
    _validate_periods(config)
    orders, products, traffic = (tables.get(k, pd.DataFrame()).copy() for k in SCHEMA)
    reasons = []
    for name, info in inspect(tables).items():
        if info["blank_cells"]:
            reasons.append(f"{name} 有 {info['blank_cells']} 个空白单元格，受影响指标按字段契约停用")
    for col in ("item_id", "order_id", "date"):
        if col not in orders or orders[col].eq("").any() or orders[col].isna().any():
            raise ValueError(f"订单缺少有效 {col}，无法安全确定清洗记录与比较时期")
    pd.to_datetime(orders.date, format="%Y-%m-%d", errors="raise")
    if orders.groupby("order_id").date.nunique().gt(1).any():
        raise ValueError("订单跨日期，无法安全确定订单时期")
    channel_ok = "channel" in orders and not orders.channel.eq("").any() and not orders.channel.isna().any()
    if channel_ok and orders.groupby("order_id").channel.nunique().gt(1).any():
        if strict:
            raise ValueError("订单跨日期或渠道，违反数据契约")
        channel_ok = False
        reasons.append("同一订单跨渠道，保留总体指标但停止渠道拆解")
    if not channel_ok:
        reasons.append("渠道字段缺失、空白或订单渠道不一致；渠道拆解不可用")

    missing_orders = sorted(set(SCHEMA["order_items"]) - set(orders.columns))
    if missing_orders:
        reasons.append("订单缺少字段 " + ", ".join(missing_orders) + "；重复与冲突仅按现有字段检查，不声称完整记录已验证")
    for col in ("gross_amount", "discount_amount", "refund_amount"):
        if col not in orders:
            reasons.append(f"{col} 缺失，对应金额指标不可用")
            continue
        try:
            orders[col + "_cents"] = orders[col].map(cents)
        except Exception as exc:
            if strict:
                raise ValueError(f"{col}: {exc}") from exc
            reasons.append(f"{col} 存在缺失或无效金额；该字段相关指标整体不可用，不对有效子集冒充全量汇总")
    money = set(orders.columns)
    if {"gross_amount_cents", "discount_amount_cents"} <= money:
        orders["pre_refund_cents"] = orders.gross_amount_cents - orders.discount_amount_cents
        if "refund_amount_cents" in money:
            orders["net_cents"] = orders.pre_refund_cents - orders.refund_amount_cents
    try:
        if "quantity" not in orders:
            raise ValueError("缺少 quantity")
        orders["quantity"] = _integers(orders.quantity, 1)
        quantity_ok = True
    except Exception as exc:
        if strict:
            raise ValueError(f"quantity 必须为有效整数: {exc}") from exc
        quantity_ok = False
        reasons.append("quantity 缺失或无效，数量指标不可用；金额本身不乘数量")

    # Only original available columns define observed record equality.
    original_cols = [col for col in SCHEMA["order_items"] if col in orders]
    unique_rows = orders.drop_duplicates(subset=original_cols).copy()
    conflicts = unique_rows.loc[unique_rows.item_id.duplicated(False), "item_id"].unique()
    excluded = orders[orders.item_id.isin(conflicts)].copy()
    cleaned = unique_rows[~unique_rows.item_id.isin(conflicts)].copy()
    duplicates = orders.groupby("item_id").size()
    exact_ids = int(((duplicates > 1) & ~duplicates.index.isin(conflicts)).sum())
    if not channel_ok:
        # Detect conflicts on original observed fields before hiding an invalid
        # dimension from downstream SQL. Otherwise channel-only conflicts could
        # accidentally become exact duplicates.
        orders, unique_rows, excluded, cleaned = (frame.drop(columns="channel", errors="ignore")
                                                for frame in (orders, unique_rows, excluded, cleaned))

    product_required = {"product_id", "category"}
    category_ok = ("product_id" in orders and not orders.product_id.eq("").any()
                   and not orders.product_id.isna().any() and product_required <= set(products.columns))
    if category_ok:
        category_ok = (not products.product_id.duplicated().any()
                       and not products[list(product_required)].eq("").any().any()
                       and not products[list(product_required)].isna().any().any())
    if strict and products.product_id.duplicated().any():
        raise ValueError("商品主键重复，不能任意选取商品记录")
    unmatched = None
    if category_ok:
        cleaned = cleaned.merge(products, on="product_id", how="left", validate="many_to_one", indicator=True)
        unknown = cleaned["_merge"].eq("left_only")
        cleaned.loc[unknown, "category"] = "未知"
        unmatched = int(unknown.sum())
        cleaned = cleaned.drop(columns="_merge").fillna("")
    else:
        reasons.append("商品关联键/品类字段缺失、空白或商品主键重复；停止品类拆解，不把整表异常伪装成未知商品")
    missing = sorted(set(SCHEMA["products"]) - set(products.columns))
    if missing:
        reasons.append("products 缺少字段 " + ", ".join(missing))

    traffic_ok = {"date", "channel", "visitors"} <= set(traffic.columns)
    traffic_issue = None
    if traffic_ok:
        try:
            if traffic[["date", "channel", "visitors"]].eq("").any().any() or traffic[["date", "channel", "visitors"]].isna().any().any():
                raise ValueError("流量有空白字段")
            pd.to_datetime(traffic.date, format="%Y-%m-%d", errors="raise")
            if traffic.duplicated(["date", "channel"]).any():
                raise ValueError("流量日期+渠道重复，不能任意加总")
            traffic["visitors"] = _integers(traffic.visitors, 0)
            days = set()
            for label in ("control", "analysis"):
                days.update(pd.date_range(config[label + "_start"], config[label + "_end"]).strftime("%Y-%m-%d"))
            channels = set(traffic.channel) | (set(orders.channel) if channel_ok else set())
            missing_keys = {(day, channel) for day in days for channel in channels} - set(zip(traffic.date, traffic.channel))
            if missing_keys:
                raise ValueError(f"流量缺少 {len(missing_keys)} 个日期渠道记录，不能将缺失访客当作零")
        except Exception as exc:
            traffic_ok, traffic_issue = False, str(exc)
    else:
        traffic_issue = "流量表或 date/channel/visitors 字段缺失"
    if not traffic_ok:
        if strict:
            raise ValueError(traffic_issue)
        reasons.append(traffic_issue + "；保留销售指标，停止访客比较")
        traffic = pd.DataFrame(columns=["date", "channel", "visitors", "period"])
    else:
        traffic = _period(traffic, config)

    orders, unique_rows, excluded, cleaned = (_period(frame, config) for frame in (orders, unique_rows, excluded, cleaned))
    flags = {"net_sales": "net_cents" in cleaned, "order_count": True,
             "pre_refund_sales": "pre_refund_cents" in cleaned,
             "refund_amount": "refund_amount_cents" in cleaned,
             "quantity": quantity_ok, "channel": channel_ok, "category": category_ok, "traffic": traffic_ok}
    flags["aov"] = flags["net_sales"]
    flags["refund_rate"] = flags["pre_refund_sales"] and flags["refund_amount"]
    availability = {"full": not reasons, "metrics": flags, "reasons": list(dict.fromkeys(reasons))}
    quality = {"raw_item_rows": len(orders), "exact_duplicate_item_ids": exact_ids,
               "duplicate_rows_removed": len(orders) - len(unique_rows),
               "conflicting_item_ids_excluded": len(conflicts), "conflict_rows_excluded": len(excluded),
               "unmatched_product_rows": unmatched, "clean_item_rows": len(cleaned),
               "excluded_raw_net_sales": int(excluded.net_cents.sum()) / 100 if flags["net_sales"] else None,
               "outside_period_rows": int(cleaned.period.eq("outside").sum()),
               "availability": availability,
               "note": "冲突排除金额是原始记录汇总，不代表已确认的真实交易损失。缺失字段时，重复/冲突仅基于现有字段。"}
    bridge = []
    for label in ("control", "analysis"):
        sums = [int(frame.loc[frame.period.eq(label), "net_cents"].sum()) / 100 if flags["net_sales"] else None
                for frame in (orders, unique_rows, cleaned)]
        bridge.append({"period": label, "raw_net_sales": sums[0],
                       "duplicate_removed": round(sums[0]-sums[1], 2) if flags["net_sales"] else None,
                       "conflict_removed_after_dedup": round(sums[1]-sums[2], 2) if flags["net_sales"] else None,
                       "clean_net_sales": sums[2]})
    quality["bridge"] = bridge
    raw = orders.copy()
    if category_ok:
        raw = raw.merge(products[["product_id", "category"]], on="product_id", how="left", validate="many_to_one")
        raw["category"] = raw.category.fillna("未知")
    quality["attribution_impact"] = _attribution_impact(raw, cleaned, flags)
    out.mkdir(parents=True, exist_ok=True)
    cleaned.to_csv(out / "clean_items.csv", index=False, encoding="utf-8-sig")
    excluded.to_csv(out / "quarantined_items.csv", index=False, encoding="utf-8-sig")
    return cleaned, traffic, quality


def summarize(df, available=None):
    available = available or {}
    net = int(df.net_cents.sum()) / 100 if "net_cents" in df else None
    pre = int(df.pre_refund_cents.sum()) / 100 if "pre_refund_cents" in df else None
    # Backward-compatible with prepared CSVs written before pre_refund_cents.
    if pre is None and {"gross_amount_cents", "discount_amount_cents"} <= set(df.columns):
        pre = int((df.gross_amount_cents-df.discount_amount_cents).sum()) / 100
    refund = int(df.refund_amount_cents.sum()) / 100 if "refund_amount_cents" in df else None
    n = int(df.order_id.nunique())
    quantity = int(df.quantity.sum()) if "quantity" in df and available.get("quantity", True) else None
    return {"net_sales": net, "order_count": n, "aov": ratio(net, n), "refund_rate": ratio(refund, pre),
            "pre_refund_sales": pre, "refund_amount": refund, "quantity": quantity}


def decomposition(control, current):
    """Two separate accounting views; neither identifies a business cause."""
    values = [control[k] for k in ("net_sales", "pre_refund_sales", "refund_amount")] + [current[k] for k in ("net_sales", "pre_refund_sales", "refund_amount")]
    bridge = {"available": all(v is not None for v in values), "pre_refund_change": None,
              "refund_change": None, "refund_effect": None, "net_change": None, "reconciles": None,
              "reason": None}
    if bridge["available"]:
        change = lambda key: (round(current[key]*100)-round(control[key]*100))/100
        bridge.update(pre_refund_change=change("pre_refund_sales"), refund_change=change("refund_amount"),
                      refund_effect=-change("refund_amount"), net_change=change("net_sales"))
        bridge["reconciles"] = round((bridge["pre_refund_change"]+bridge["refund_effect"]-bridge["net_change"])*100) == 0
    else:
        bridge["reason"] = "净销售额、退款前销售额或退款金额不可用"
    order = {"available": control["aov"] is not None and current["aov"] is not None,
             "order_effect": None, "aov_effect": None, "net_change": None, "reconciles": None,
             "reason": None, "note": "订单量与净客单价的对称数学分解；净客单价已扣退款，不能再与退款金额项相加，不表示因果效应。"}
    if order["available"]:
        n0, n1 = control["order_count"], current["order_count"]
        a0, a1 = Decimal(str(control["net_sales"]))/n0, Decimal(str(current["net_sales"]))/n1
        n_effect = Decimal(n1-n0)*(a0+a1)/2
        a_effect = (a1-a0)*Decimal(n0+n1)/2
        delta = Decimal(str(current["net_sales"]))-Decimal(str(control["net_sales"]))
        order.update(order_effect=float(n_effect), aov_effect=float(a_effect), net_change=float(delta),
                     reconciles=abs(n_effect+a_effect-delta) < Decimal("0.0000001"))
    else:
        order["reason"] = "净销售额不可用或某期订单数为零，净客单价无定义"
    return {"refund_bridge": bridge, "order_aov": order}


def compare(df, available=None):
    control = summarize(df[df.period.eq("control")], available)
    current = summarize(df[df.period.eq("analysis")], available)
    delta = round(current["net_sales"] - control["net_sales"], 2) if control["net_sales"] is not None and current["net_sales"] is not None else None
    return {"control": control, "analysis": current, "difference": delta, "change_rate": ratio(delta, control["net_sales"]),
            "decomposition": decomposition(control, current)}


def _ranking(df, dim, total):
    rows = []
    for name, group in df.groupby(dim):
        d = (int(group.loc[group.period.eq("analysis"), "net_cents"].sum())-int(group.loc[group.period.eq("control"), "net_cents"].sum()))/100
        rows.append({"group": str(name), "difference": d, "contribution": ratio(d, total) if total < 0 else None})
    return sorted(rows, key=lambda row: (row["difference"], row["group"]))


def _attribution_impact(raw, cleaned, available):
    raw, cleaned = (frame[frame.period.ne("outside")] for frame in (raw, cleaned))
    result = {"available": available["net_sales"], "raw_difference": None, "clean_difference": None,
              "difference_adjustment": None, "dimensions": {}, "unknown_category": {"rows": None,
              "control_net_sales": None, "analysis_net_sales": None, "difference": None,
              "note": "未匹配商品金额保留为未知品类；已知品类排名不证明未知记录真实所属品类。"},
              "note": "仅比较本次输入按既定规则清洗前后的账面汇总。原始行含重复/冲突，不是真实交易基准；来源未变不代表对其他异常普遍稳健。"}
    if available["net_sales"]:
        delta = lambda frame: (int(frame.loc[frame.period.eq("analysis"), "net_cents"].sum())-int(frame.loc[frame.period.eq("control"), "net_cents"].sum()))/100
        result.update(raw_difference=delta(raw), clean_difference=delta(cleaned), difference_adjustment=round(delta(cleaned)-delta(raw), 2))
    for dim in ("channel", "category"):
        ok = available["net_sales"] and available[dim]
        item = {"available": ok, "raw_top_decline_source": None, "clean_top_decline_source": None,
                "source_changed": None, "raw_ranking": [], "clean_ranking": [], "rank_changed": None,
                "raw_tied_sources": [], "clean_tied_sources": []}
        if ok:
            for label, frame in (("raw", raw), ("clean", cleaned)):
                rows = _ranking(frame, dim, result[label+"_difference"])
                item[label+"_ranking"] = rows
                tied = [r["group"] for r in rows if r["difference"] == rows[0]["difference"]] if rows and result[label+"_difference"] < 0 else []
                item[label+"_tied_sources"] = tied
                item[label+"_top_decline_source"] = tied[0] if len(tied) == 1 else None
            item["source_changed"] = set(item["raw_tied_sources"]) != set(item["clean_tied_sources"])
            # Ranking concerns ordering/tie relationships, not amount changes alone.
            rank = lambda rows: [(r["group"], sum(x["difference"] < r["difference"] for x in rows)) for r in sorted(rows,key=lambda r:r["group"])]
            item["rank_changed"] = rank(item["raw_ranking"]) != rank(item["clean_ranking"])
        result["dimensions"][dim] = item
    if available["category"] and available["net_sales"]:
        unknown = cleaned[cleaned.category.eq("未知")]
        a = int(unknown.loc[unknown.period.eq("control"), "net_cents"].sum())/100
        b = int(unknown.loc[unknown.period.eq("analysis"), "net_cents"].sum())/100
        result["unknown_category"].update(rows=len(unknown), control_net_sales=a, analysis_net_sales=b, difference=round(b-a, 2))
    return result


def compute(df, traffic, quality, out: Path):
    df = df[df.period.ne("outside")]
    availability = quality.get("availability", {"full": True, "metrics": {}, "reasons": []})
    flags = availability["metrics"]
    overall = compare(df, flags)
    result = {"overall": overall, "data_quality": quality, "availability": availability,
              "by_channel": {}, "by_category": {}, "traffic_by_channel": {}}
    for dim in ("channel", "category"):
        if dim not in df or not flags.get(dim, True):
            continue
        for name, g in df.groupby(dim):
            pair = compare(g, flags)
            delta = pair["difference"]
            result["by_"+dim][name] = {"control_net_sales": pair["control"]["net_sales"], "analysis_net_sales": pair["analysis"]["net_sales"],
                "difference": delta, "decrease_amount": -delta if delta is not None else None,
                "contribution": ratio(delta, overall["difference"]) if overall["difference"] is not None and overall["difference"] < 0 else None,
                "control": pair["control"], "analysis": pair["analysis"], "decomposition": pair["decomposition"]}
    if flags.get("traffic", True):
        for name, g in traffic.groupby("channel"):
            a = int(g.loc[g.period.eq("control"), "visitors"].sum())
            b = int(g.loc[g.period.eq("analysis"), "visitors"].sum())
            result["traffic_by_channel"][name] = {"control_visits": a, "analysis_visits": b, "difference": b-a}
    result["top_decline_source"] = {dim: min(result["by_"+dim], key=lambda k: result["by_"+dim][k]["difference"])
        if overall["difference"] is not None and overall["difference"] < 0 and result["by_"+dim] else None for dim in ("channel", "category")}
    out.mkdir(parents=True, exist_ok=True)
    for dim in ("channel", "category"):
        pd.DataFrame([{"group": k, **{f:p[f] for f in ("control_net_sales", "analysis_net_sales", "difference", "contribution")}}
                      for k,p in result["by_"+dim].items()], columns=["group", "control_net_sales", "analysis_net_sales", "difference", "contribution"]).to_csv(out / f"by_{dim}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"period": k, **overall[k]} for k in ("control", "analysis")]).to_csv(out / "overall.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(quality["bridge"]).to_csv(out / "quality_bridge.csv", index=False, encoding="utf-8-sig")
    if "net_cents" in df:
        daily = df.groupby("date", as_index=False).net_cents.sum()
        daily["net_sales"] = daily.net_cents / 100
    else:
        daily = pd.DataFrame(columns=["date", "net_cents", "net_sales"])
    daily.to_csv(out / "daily.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame.from_dict(result["traffic_by_channel"], orient="index", columns=["control_visits", "analysis_visits", "difference"]).rename_axis("channel").to_csv(out / "traffic.csv", encoding="utf-8-sig")
    decompositions = []
    for dim, groups in (("overall", {"总体": overall}), ("channel", result["by_channel"]), ("category", result["by_category"])):
        for group, pair in groups.items():
            for view, values in pair["decomposition"].items():
                decompositions.append({"dimension": dim, "group": group, "view": view, **values})
    pd.DataFrame(decompositions).to_csv(out / "decomposition.csv", index=False, encoding="utf-8-sig")
    impact = quality.get("attribution_impact", {})
    dump(out.parent / "attribution_impact.json", impact)
    impact_rows = []
    for dim, values in impact.get("dimensions", {}).items():
        for stage in ("raw", "clean"):
            impact_rows.extend({"dimension": dim, "stage": stage, **row} for row in values[stage+"_ranking"])
    pd.DataFrame(impact_rows, columns=["dimension", "stage", "group", "difference", "contribution"]).to_csv(out / "attribution_impact.csv", index=False, encoding="utf-8-sig")
    return result


def verify(metrics):
    delta = metrics["overall"]["difference"]
    flags = metrics.get("availability", {}).get("metrics", {})
    checks, skipped = {}, {}
    for dim in ("channel", "category"):
        key = dim+"_reconciles"
        if delta is None or not flags.get(dim, True):
            skipped[key] = "净销售额或该维度不可用"
        else:
            checks[key] = sum(round(v["difference"]*100) for v in metrics["by_"+dim].values()) == round(delta*100)
    bridge = metrics["data_quality"]["bridge"]
    if all(r["clean_net_sales"] is not None for r in bridge):
        checks["quality_bridge_reconciles"] = all(round((r["raw_net_sales"]-r["duplicate_removed"]-r["conflict_removed_after_dedup"]-r["clean_net_sales"])*100)==0 for r in bridge)
    else:
        skipped["quality_bridge_reconciles"] = "净销售额不可用"
    for dim, groups in (("overall", {"overall": metrics["overall"]}), ("channel", metrics["by_channel"]), ("category", metrics["by_category"])):
        for group, pair in groups.items():
            for view, values in pair["decomposition"].items():
                key = f"{dim}.{group}.{view}_reconciles"
                if values["available"]:
                    checks[key] = values["reconciles"]
                else:
                    skipped[key] = values["reason"]
    return {"passed": all(checks.values()), "complete": metrics.get("availability", {}).get("full", True),
            "checks": checks, "skipped": skipped,
            "note": "passed 仅表示已执行的检查通过；skipped 不视为已验证，complete=false 时仍为部分结果。"}
