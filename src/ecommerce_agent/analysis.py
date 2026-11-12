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
    return float(a / b) if b else None

def cents(s):
    v = Decimal(str(s)) * 100
    if not v.is_finite() or v != v.to_integral_value() or v < 0:
        raise ValueError("金额必须是非负且精确到分的数值")
    return int(v)

def read_tables(folder: Path):
    # Only these three exact paths are opened. No model-provided paths.
    return {name: pd.read_csv(folder / f"{name}.csv", dtype=str, encoding="utf-8-sig", keep_default_na=False) for name in SCHEMA}

def inspect(tables):
    return {name: {"rows": len(t), "columns": list(t.columns),
                   "missing_columns": sorted(set(SCHEMA[name]) - set(t.columns)),
                   "blank_cells": int(t.eq("").sum().sum()), "sample": t.head(2).to_dict("records")}
            for name, t in tables.items()}

def clean(tables, out: Path, config):
    for name, info in inspect(tables).items():
        if info["missing_columns"] or info["blank_cells"]:
            raise ValueError(f"{name}: missing_columns={info['missing_columns']}, blank_cells={info['blank_cells']}; 无法补造数据，请报告受影响指标")
    orders, products, traffic = (tables[k].copy() for k in SCHEMA)
    if products.product_id.duplicated().any():
        raise ValueError("商品主键重复，不能任意选取商品记录")
    if traffic.duplicated(["date", "channel"]).any():
        raise ValueError("流量日期+渠道重复，不能任意加总")
    if (orders.groupby("order_id")[["date", "channel"]].nunique() > 1).any().any():
        raise ValueError("订单跨日期或渠道，违反数据契约")
    for frame in (orders, traffic):
        pd.to_datetime(frame.date, format="%Y-%m-%d", errors="raise")
    for field in ("control_start", "control_end", "analysis_start", "analysis_end"):
        pd.to_datetime(config[field], format="%Y-%m-%d", errors="raise")
    if not config["control_start"] <= config["control_end"] < config["analysis_start"] <= config["analysis_end"]:
        raise ValueError("时期必须有序且不重叠")
    expected_days = set()
    for period in ("control", "analysis"):
        expected_days.update(pd.date_range(config[period+"_start"],config[period+"_end"]).strftime("%Y-%m-%d"))
    channels = set(orders.channel) | set(traffic.channel)
    observed_keys = set(zip(traffic.date, traffic.channel))
    missing_keys = {(day, channel) for day in expected_days for channel in channels} - observed_keys
    if missing_keys:
        raise ValueError(f"流量缺少 {len(missing_keys)} 个日期渠道记录，不能将缺失访客当作零")
    for frame, field, minimum in ((orders, "quantity", 1), (traffic, "visitors", 0)):
        values = pd.to_numeric(frame[field], errors="raise")
        if ((values % 1 != 0) | (values < minimum)).any():
            raise ValueError(f"{field} 必须为有效整数")
        frame[field] = values.astype("int64")
    for field in ("gross_amount", "discount_amount", "refund_amount"):
        orders[field + "_cents"] = orders[field].map(cents)
    orders["net_cents"] = orders.gross_amount_cents - orders.discount_amount_cents - orders.refund_amount_cents
    unique_rows = orders.drop_duplicates(subset=SCHEMA["order_items"])
    conflicts = unique_rows.loc[unique_rows.item_id.duplicated(False), "item_id"].unique()
    excluded = orders[orders.item_id.isin(conflicts)].copy()
    cleaned = unique_rows[~unique_rows.item_id.isin(conflicts)].copy()
    duplicates = orders.groupby("item_id").size()
    exact_ids = int(((duplicates > 1) & ~duplicates.index.isin(conflicts)).sum())
    cleaned = cleaned.merge(products, on="product_id", how="left", validate="many_to_one", indicator=True)
    unknown = cleaned["_merge"].eq("left_only")
    cleaned.loc[unknown, "category"] = "未知"
    cleaned = cleaned.drop(columns="_merge").fillna("")
    for frame in (orders, unique_rows, excluded, cleaned, traffic):
        frame["period"] = "outside"
        for label in ("control", "analysis"):
            mask = frame.date.between(config[label + "_start"], config[label + "_end"])
            frame.loc[mask, "period"] = label
    quality = {"raw_item_rows": len(orders), "exact_duplicate_item_ids": exact_ids,
               "duplicate_rows_removed": len(orders) - len(unique_rows),
               "conflicting_item_ids_excluded": len(conflicts), "conflict_rows_excluded": len(excluded),
               "unmatched_product_rows": int(unknown.sum()), "clean_item_rows": len(cleaned),
               "excluded_raw_net_sales": int(excluded.net_cents.sum()) / 100,
               "outside_period_rows": int(cleaned.period.eq("outside").sum()),
               "note": "冲突排除金额是原始记录汇总，不代表已确认的真实交易损失。"}
    bridge = []
    for period in ("control", "analysis"):
        raw_sum = int(orders.loc[orders.period.eq(period), "net_cents"].sum())
        dedup_sum = int(unique_rows.loc[unique_rows.period.eq(period), "net_cents"].sum())
        clean_sum = int(cleaned.loc[cleaned.period.eq(period), "net_cents"].sum())
        bridge.append({"period": period, "raw_net_sales": raw_sum/100, "duplicate_removed": (raw_sum-dedup_sum)/100,
                       "conflict_removed_after_dedup": (dedup_sum-clean_sum)/100, "clean_net_sales": clean_sum/100})
    quality["bridge"] = bridge
    out.mkdir(parents=True, exist_ok=True)
    cleaned.to_csv(out / "clean_items.csv", index=False, encoding="utf-8-sig")
    excluded.to_csv(out / "quarantined_items.csv", index=False, encoding="utf-8-sig")
    return cleaned, traffic, quality

def summarize(df):
    net = int(df.net_cents.sum()) / 100
    pre = int((df.gross_amount_cents - df.discount_amount_cents).sum()) / 100
    refund = int(df.refund_amount_cents.sum()) / 100
    n = int(df.order_id.nunique())
    return {"net_sales": net, "order_count": n, "aov": ratio(net, n), "refund_rate": ratio(refund, pre),
            "pre_refund_sales": pre, "refund_amount": refund, "quantity": int(df.quantity.sum())}

def compare(df):
    control = summarize(df[df.period.eq("control")])
    analysis = summarize(df[df.period.eq("analysis")])
    delta = round(analysis["net_sales"] - control["net_sales"], 2)
    return {"control": control, "analysis": analysis, "difference": delta, "change_rate": ratio(delta, control["net_sales"])}

def compute(df, traffic, quality, out: Path):
    df = df[df.period.ne("outside")]
    overall = compare(df)
    result = {"overall": overall, "data_quality": quality, "by_channel": {}, "by_category": {}, "traffic_by_channel": {}}
    for dim in ("channel", "category"):
        for name, g in df.groupby(dim):
            pair = compare(g)
            result["by_"+dim][name] = {"control_net_sales": pair["control"]["net_sales"], "analysis_net_sales": pair["analysis"]["net_sales"],
                "difference": pair["difference"], "decrease_amount": -pair["difference"],
                "contribution": ratio(pair["difference"], overall["difference"]) if overall["difference"] < 0 else None,
                "control": pair["control"], "analysis": pair["analysis"]}
    for name, g in traffic.groupby("channel"):
        a = int(g.loc[g.period.eq("control"), "visitors"].sum())
        b = int(g.loc[g.period.eq("analysis"), "visitors"].sum())
        result["traffic_by_channel"][name] = {"control_visits": a, "analysis_visits": b, "difference": b-a}
    result["top_decline_source"] = {dim: min(result["by_"+dim], key=lambda k: result["by_"+dim][k]["difference"])
        if overall["difference"] < 0 and result["by_"+dim] else None for dim in ("channel", "category")}
    out.mkdir(parents=True, exist_ok=True)
    for dim in ("channel", "category"):
        pd.DataFrame([{"group": k, **{f:p[f] for f in ("control_net_sales", "analysis_net_sales", "difference", "contribution")}}
                      for k,p in result["by_"+dim].items()]).to_csv(out / f"by_{dim}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"period": k, **overall[k]} for k in ("control", "analysis")]).to_csv(out / "overall.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(quality["bridge"]).to_csv(out / "quality_bridge.csv", index=False, encoding="utf-8-sig")
    daily = df.groupby("date", as_index=False).net_cents.sum()
    daily["net_sales"] = daily.net_cents / 100
    daily.to_csv(out / "daily.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame.from_dict(result["traffic_by_channel"], orient="index").rename_axis("channel").to_csv(out / "traffic.csv", encoding="utf-8-sig")
    return result

def verify(metrics):
    total = round(metrics["overall"]["difference"] * 100)
    checks = {dim+"_reconciles": sum(round(v["difference"]*100) for v in metrics["by_"+dim].values()) == total
              for dim in ("channel", "category")}
    checks["quality_bridge_reconciles"] = all(round((r["raw_net_sales"]-r["duplicate_removed"]-r["conflict_removed_after_dedup"]-r["clean_net_sales"])*100)==0 for r in metrics["data_quality"]["bridge"])
    return {"passed": all(checks.values()), "checks": checks}
