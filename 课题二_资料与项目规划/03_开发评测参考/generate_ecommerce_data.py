#!/usr/bin/env python3
"""生成电商销售下降分析课程数据，并计算教师参考答案。仅依赖 Python 标准库。"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

CHANNELS = ["App", "网页", "搜索", "联盟"]
CATEGORIES = ["数码", "家居", "服饰", "美妆", "食品"]
BASE_VISITORS = {"App": 1400, "网页": 1000, "搜索": 1200, "联盟": 800}
BASE_CONVERSION = {"App": 0.075, "网页": 0.068, "搜索": 0.072, "联盟": 0.062}
PUBLIC_SCENARIOS = [
    {"kind": "traffic_drop", "target_channel": "搜索"},
    {"kind": "refund_spike", "target_category": "数码"},
    {"kind": "aov_drop", "target_category": "服饰"},
]


def ensure_empty_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise SystemExit(f"输出目录非空：{path}。请换一个新目录，避免覆盖已有文件。")
    path.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def make_products(rng: random.Random) -> list[dict]:
    ranges = {
        "数码": (120, 1200), "家居": (40, 500), "服饰": (30, 400),
        "美妆": (25, 350), "食品": (10, 180),
    }
    products = []
    pid = 1
    for category in CATEGORIES:
        low, high = ranges[category]
        for index in range(12):
            price = round(rng.uniform(low, high), 2)
            products.append({
                "product_id": f"P{pid:04d}",
                "product_name": f"{category}商品{index + 1:02d}",
                "category": category,
                "list_price": price,
            })
            pid += 1
    return products


def weighted_choice(rng: random.Random, values: list[str], weights: list[float]) -> str:
    return rng.choices(values, weights=weights, k=1)[0]


def binomial(rng: random.Random, n: int, p: float) -> int:
    return sum(rng.random() < p for _ in range(n))


def generate_case(case_dir: Path, seed: int, scenario: dict) -> dict:
    rng = random.Random(seed)
    products = make_products(rng)
    by_category = {
        category: [p for p in products if p["category"] == category]
        for category in CATEGORIES
    }
    start = date(2026, 9, 1)
    traffic_rows: list[dict] = []
    item_rows: list[dict] = []
    order_no = 1
    item_no = 1
    category_weights = [0.20, 0.19, 0.23, 0.17, 0.21]

    for day_index in range(28):
        current = start + timedelta(days=day_index)
        is_analysis = day_index >= 14
        weekend_factor = 1.08 if current.weekday() >= 5 else 1.0
        for channel in CHANNELS:
            visitor_factor = weekend_factor
            if is_analysis:
                visitor_factor *= 0.99
                if scenario["kind"] == "traffic_drop" and channel == scenario["target_channel"]:
                    visitor_factor *= 0.58
            visitors = max(100, round(rng.gauss(BASE_VISITORS[channel] * visitor_factor, 35)))
            traffic_rows.append({
                "date": current.isoformat(),
                "channel": channel,
                "visitors": visitors,
            })
            orders = binomial(rng, visitors, BASE_CONVERSION[channel])
            for _ in range(orders):
                order_id = f"O{order_no:07d}"
                order_no += 1
                line_count = 2 if rng.random() < 0.22 else 1
                for _line in range(line_count):
                    category = weighted_choice(rng, CATEGORIES, category_weights)
                    candidates = by_category[category]
                    quantity = 2 if rng.random() < 0.18 else 1
                    discount_rate = rng.uniform(0.03, 0.13)

                    if is_analysis and scenario["kind"] == "aov_drop" and category == scenario["target_category"]:
                        candidates = sorted(candidates, key=lambda p: p["list_price"])[:5]
                        quantity = 1
                        discount_rate = rng.uniform(0.18, 0.28)
                    product = rng.choice(candidates)
                    unit_price = product["list_price"] * rng.uniform(0.96, 1.04)
                    gross = round(unit_price * quantity, 2)
                    discount = round(gross * discount_rate, 2)
                    refund_probability = 0.035
                    if is_analysis and scenario["kind"] == "refund_spike" and category == scenario["target_category"]:
                        refund_probability = 0.52
                    refunded = rng.random() < refund_probability
                    refund = round((gross - discount) * rng.choice([0.5, 1.0]), 2) if refunded else 0.0
                    item_rows.append({
                        "order_id": order_id,
                        "item_id": f"I{item_no:08d}",
                        "date": current.isoformat(),
                        "product_id": product["product_id"],
                        "channel": channel,
                        "quantity": quantity,
                        "gross_amount": f"{gross:.2f}",
                        "discount_amount": f"{discount:.2f}",
                        "refund_amount": f"{refund:.2f}",
                    })
                    item_no += 1

    indices = list(range(len(item_rows)))
    rng.shuffle(indices)
    exact_indices = indices[:12]
    conflict_indices = indices[12:16]
    missing_indices = indices[16:24]
    for idx in missing_indices:
        item_rows[idx]["product_id"] = "P9999"
    exact_duplicates = [dict(item_rows[idx]) for idx in exact_indices]
    conflict_duplicates = []
    for idx in conflict_indices:
        changed = dict(item_rows[idx])
        changed["discount_amount"] = f"{float(changed['discount_amount']) + 1.23:.2f}"
        conflict_duplicates.append(changed)
    item_rows.extend(exact_duplicates)
    item_rows.extend(conflict_duplicates)
    rng.shuffle(item_rows)

    case_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        case_dir / "products.csv", products,
        ["product_id", "product_name", "category", "list_price"],
    )
    write_csv(case_dir / "traffic.csv", traffic_rows, ["date", "channel", "visitors"])
    write_csv(
        case_dir / "order_items.csv", item_rows,
        ["order_id", "item_id", "date", "product_id", "channel", "quantity",
         "gross_amount", "discount_amount", "refund_amount"],
    )
    return calculate_answer(item_rows, products, traffic_rows, scenario, seed)


def calculate_answer(
    rows: list[dict], products: list[dict], traffic: list[dict], scenario: dict, seed: int
) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["item_id"]].append(row)
    clean = []
    duplicate_ids = 0
    conflict_ids = 0
    for item_id, group in grouped.items():
        signatures = {tuple(sorted(row.items())) for row in group}
        if len(signatures) == 1:
            clean.append(group[0])
            if len(group) > 1:
                duplicate_ids += 1
        else:
            conflict_ids += 1

    product_map = {p["product_id"]: p for p in products}
    enriched = []
    for row in clean:
        gross = float(row["gross_amount"])
        discount = float(row["discount_amount"])
        refund = float(row["refund_amount"])
        enriched.append({
            **row,
            "gross": gross,
            "discount": discount,
            "refund": refund,
            "net_sales": gross - discount - refund,
            "category": product_map.get(row["product_id"], {}).get("category", "未知"),
            "period": "control" if row["date"] < "2026-09-15" else "analysis",
        })

    def summarize(items: list[dict]) -> dict:
        gross = sum(x["gross"] for x in items)
        discount = sum(x["discount"] for x in items)
        refund = sum(x["refund"] for x in items)
        net = gross - discount - refund
        order_count = len({x["order_id"] for x in items})
        denominator = gross - discount
        return {
            "net_sales": round(net, 2),
            "order_count": order_count,
            "aov": round(net / order_count, 4) if order_count else None,
            "refund_rate": round(refund / denominator, 6) if denominator else None,
        }

    control = [x for x in enriched if x["period"] == "control"]
    analysis = [x for x in enriched if x["period"] == "analysis"]
    control_summary = summarize(control)
    analysis_summary = summarize(analysis)
    difference = round(analysis_summary["net_sales"] - control_summary["net_sales"], 2)
    change_rate = round(difference / control_summary["net_sales"], 6)

    def breakdown(key: str) -> dict:
        values = sorted({x[key] for x in enriched})
        result = {}
        for value in values:
            before = summarize([x for x in control if x[key] == value])["net_sales"]
            after = summarize([x for x in analysis if x[key] == value])["net_sales"]
            result[value] = {
                "control_net_sales": before,
                "analysis_net_sales": after,
                "difference": round(after - before, 2),
                "decrease_amount": round(before - after, 2),
            }
        return result

    channel_breakdown = breakdown("channel")
    category_breakdown = breakdown("category")
    traffic_by_channel = {}
    for channel in CHANNELS:
        before = sum(int(x["visitors"]) for x in traffic if x["channel"] == channel and x["date"] < "2026-09-15")
        after = sum(int(x["visitors"]) for x in traffic if x["channel"] == channel and x["date"] >= "2026-09-15")
        traffic_by_channel[channel] = {
            "control_visits": before,
            "analysis_visits": after,
            "difference": after - before,
        }

    top_channel = max(channel_breakdown, key=lambda x: channel_breakdown[x]["decrease_amount"])
    top_category = max(category_breakdown, key=lambda x: category_breakdown[x]["decrease_amount"])
    return {
        "seed": seed,
        "scenario_for_teacher": scenario,
        "periods": {"control": "2026-09-01~2026-09-14", "analysis": "2026-09-15~2026-09-28"},
        "overall": {
            "control": control_summary,
            "analysis": analysis_summary,
            "difference": difference,
            "change_rate": change_rate,
        },
        "top_decline_source": {"channel": top_channel, "category": top_category},
        "by_channel": channel_breakdown,
        "by_category": category_breakdown,
        "traffic_by_channel": traffic_by_channel,
        "data_quality": {
            "exact_duplicate_item_ids": duplicate_ids,
            "conflicting_item_ids_excluded": conflict_ids,
            "unmatched_product_rows": sum(x["product_id"] not in product_map for x in clean),
            "clean_item_rows": len(clean),
        },
    }


def hidden_scenarios(rng: random.Random, count: int) -> list[dict]:
    scenarios = []
    for _ in range(count):
        kind = rng.choice(["traffic_drop", "refund_spike", "aov_drop"])
        if kind == "traffic_drop":
            scenarios.append({"kind": kind, "target_channel": rng.choice(CHANNELS)})
        else:
            scenarios.append({"kind": kind, "target_category": rng.choice(CATEGORIES)})
    return scenarios


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["public", "hidden"], default="public")
    parser.add_argument("--output", help="默认按模式选择 data/ecommerce_public 或 data/ecommerce_hidden")
    parser.add_argument("--seed", type=int, help="public 默认 20261018；hidden 必须显式传入私有整数")
    parser.add_argument("--count", type=int, default=2, help="hidden 模式的数据集数量")
    args = parser.parse_args()

    if args.mode == "hidden" and args.seed is None:
        parser.error("hidden 模式必须通过 --seed 传入未公开的私有整数")
    seed = 20261018 if args.seed is None else args.seed
    default_output = "data/ecommerce_public" if args.mode == "public" else "data/ecommerce_hidden"
    output = Path(args.output or default_output).resolve()
    ensure_empty_output(output)
    if args.mode == "public":
        scenarios = PUBLIC_SCENARIOS
    else:
        scenarios = hidden_scenarios(random.Random(seed), args.count)

    answers = {}
    for index, scenario in enumerate(scenarios, start=1):
        name = f"case_{index:02d}"
        answers[name] = generate_case(output / name, seed + index * 1009, scenario)
        print(f"已生成 {name}")

    answer_file = output / ("public_reference_answers.json" if args.mode == "public" else "teacher_hidden_answers.json")
    answer_file.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "README.txt").write_text(
        "每个 case 目录包含 products.csv、traffic.csv、order_items.csv。\n"
        "public 模式的 reference answers 可供开发核对。\n"
        "hidden 模式的整个目录和答案仅供教师保存，不应发给学生。\n",
        encoding="utf-8",
    )
    print(f"参考答案：{answer_file}")
    print(f"完成：{output}")


if __name__ == "__main__":
    main()
