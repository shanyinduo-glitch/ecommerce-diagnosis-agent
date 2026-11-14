"""Evidence-based reports and high-resolution figures; no model-written numbers."""
from __future__ import annotations
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from .io import dump, sha


def display(v, percent=False):
    return "不可计算" if v is None else f"{v:.2%}" if percent else f"{v:,.2f}"


def md(v):
    return str(v).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def charts(metrics, output: Path, config):
    """PNG 300 dpi and SVG, with captions and hashes of source evidence."""
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Noto Sans CJK JP", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    dest = output / "charts"
    dest.mkdir(parents=True, exist_ok=True)
    comparison = f"分析期 {config['analysis_start']}—{config['analysis_end']} 减对比期 {config['control_start']}—{config['control_end']}"
    files, entries = [], []

    def save(fig, name, caption, sources, available=True):
        fig.savefig(dest / f"{name}.png", dpi=300)
        fig.savefig(dest / f"{name}.svg")
        plt.close(fig)
        files.append(f"charts/{name}.png")
        entries.append({"id": name, "png": f"charts/{name}.png", "svg": f"charts/{name}.svg", "available": available,
                        "caption": caption, "comparison": comparison, "unit": "元", "png_sha256": sha(dest / f"{name}.png"),
                        "source_hashes": {p: sha(output / p) for p in sources if (output / p).exists()}})

    def empty(ax, label):
        ax.text(.5, .5, f"{label}不可计算\n缺失指标未按零填补，请查看质量报告", ha="center", va="center", transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])

    daily_path = output / "tables/daily.csv"
    daily = pd.read_csv(daily_path) if daily_path.exists() else pd.DataFrame()
    available = not daily.empty and "net_sales" in daily and daily.net_sales.notna().any()
    fig, ax = plt.subplots(figsize=(10, 4.8), layout="constrained")
    if available:
        ax.plot(pd.to_datetime(daily.date), daily.net_sales, marker="o", markersize=3, color="#2563eb", label="清洗后净销售额")
        ax.axvline(pd.Timestamp(config["analysis_start"]), color="#db2777", linestyle="--", label="分析期开始")
        ax.legend(); ax.grid(alpha=.2); fig.autofmt_xdate()
    else:
        empty(ax, "每日净销售额")
    ax.set(title=f"每日净销售额（清洗后）\n{config['control_start']}—{config['analysis_end']}", xlabel="订单日期", ylabel="净销售额（元）")
    save(fig, "daily_net_sales", "清洗后每日净销售额；虚线标记分析期开始。", ["metrics.json", "tables/daily.csv"], bool(available))
    for dim, label in (("channel", "渠道"), ("category", "品类")):
        values = sorted(((k, v) for k, v in metrics.get("by_" + dim, {}).items() if v.get("difference") is not None), key=lambda x: x[1]["difference"])
        fig, ax = plt.subplots(figsize=(10, 4.8), layout="constrained")
        if values:
            ax.barh([k for k, v in values], [v["difference"] for k, v in values], color=["#dc2626" if v["difference"] < 0 else "#059669" for k, v in values])
            ax.axvline(0, color="#64748b", linewidth=.8)
        else:
            empty(ax, f"{label}净销售额变化")
        ax.set(title=f"{label}净销售额变化\n{comparison}", xlabel="净销售额差额（元）")
        save(fig, f"{dim}_delta", f"{label}拆解视角；红色减少、绿色增加。渠道与品类贡献不能相加。", ["metrics.json", f"tables/by_{dim}.csv"], bool(values))
    decomp = metrics.get("overall", {}).get("decomposition", {})
    for name, title, keys, labels, caption in (
        ("refund_bridge", "总体退款账面桥接", ["pre_refund_change", "refund_effect"], ["退款前销售额变化", "退款变化的账面项（取负）"], "退款前销售额变化减退款变化等于净销售额变化；不能说明退款的业务原因。"),
        ("order_aov", "总体订单量与净客单价分解", ["order_effect", "aov_effect"], ["订单量项", "净客单价项"], "两因素对称分解；净客单价已包含退款，不与退款桥接各项再次相加。"),
    ):
        value = decomp.get(name, {})
        if not value.get("available"):
            continue
        amounts = [value[k] for k in keys] + [value["net_change"]]
        fig, ax = plt.subplots(figsize=(10, 4.8), layout="constrained")
        ax.bar(labels + ["净销售额变化"], amounts, color=["#059669" if v >= 0 else "#dc2626" for v in amounts])
        ax.margins(y=.18)
        ax.axhline(0, color="#64748b", linewidth=.8)
        for idx, amount in enumerate(amounts):
            ax.annotate(f"{amount:+,.2f}", (idx, amount), xytext=(0, 5 if amount >= 0 else -14), textcoords="offset points", ha="center", fontsize=9)
        ax.set(title=f"{title}\n{comparison}", ylabel="金额变化（元）")
        save(fig, name, caption, ["metrics.json", "tables/decomposition.csv"])
    impact = metrics.get("data_quality", {}).get("attribution_impact", {})
    for dim, label in (("channel", "渠道"), ("category", "品类")):
        value = impact.get("dimensions", {}).get(dim, {})
        if not value.get("available"):
            continue
        raw = {r["group"]: r["difference"] for r in value.get("raw_ranking", [])}
        clean = {r["group"]: r["difference"] for r in value.get("clean_ranking", [])}
        groups = sorted(set(raw) | set(clean), key=lambda k: clean.get(k, 0))
        if not groups:
            continue
        fig, ax = plt.subplots(figsize=(10, 5.2), layout="constrained")
        positions = list(range(len(groups)))
        ax.barh([p - .18 for p in positions], [raw.get(k, 0) for k in groups], height=.35, label="原始行汇总（含异常）", color="#94a3b8")
        ax.barh([p + .18 for p in positions], [clean.get(k, 0) for k in groups], height=.35, label="按课程规则清洗后", color="#2563eb")
        ax.set_yticks(positions, groups); ax.axvline(0, color="#64748b", linewidth=.8); ax.legend()
        ax.set(title=f"清洗对{label}销售差额的影响\n{comparison}", xlabel="净销售额差额（元）")
        save(fig, f"quality_{dim}_impact", "原始行汇总仅用于展示异常影响，不是真实交易额或替代清洗口径。", ["metrics.json", "attribution_impact.json"])
    dump(output / "figure_index.json", {"comparison": comparison, "figures": entries,
         "note": "PNG 300 dpi，SVG 可编辑缩放；请连同图注、时期、单位、来源使用。available=false 为不可计算提示图，不代表零值。"})
    return files


def _decomposition(pair, title):
    text = []
    parts = pair.get("decomposition", {})
    bridge = parts.get("refund_bridge", {})
    if bridge.get("available"):
        text += [f"### {md(title)}", "", f"退款前销售额变化 {display(bridge['pre_refund_change'])} 元 − 退款金额变化 {display(bridge['refund_change'])} 元 = 净销售额变化 {display(bridge['net_change'])} 元。", ""]
    split = parts.get("order_aov", {})
    if split.get("available"):
        text += [f"订单量项 {display(split['order_effect'])} 元 + 净客单价项 {display(split['aov_effect'])} 元 = 净销售额变化 {display(split['net_change'])} 元。", ""]
    return text


def report(output, metrics, quality, findings, status, errors, config, usage):
    text = ["# 电商销售变化诊断报告", "", f"运行状态：**{status}**。", "",
            f"对比期：{config['control_start']} 至 {config['control_end']}；分析期：{config['analysis_start']} 至 {config['analysis_end']}。金额单位为元。", ""]
    availability = (metrics or {}).get("availability") or (quality or {}).get("availability", {})
    reasons = availability.get("reasons", [])
    if reasons:
        text += ["## 未完成指标与原因", "", "保留可核算指标；缺失项不按零填补。", ""]
        text += [f"- {md(k)}：{md(v)}" for k, v in reasons.items()] if isinstance(reasons, dict) else [f"- {md(v)}" for v in reasons]
        text.append("")
    if metrics and metrics.get("overall"):
        overall = metrics["overall"]
        text += ["## 总体变化", "", f"净销售额由 {display(overall['control'].get('net_sales'))} 变为 {display(overall['analysis'].get('net_sales'))}，差额 {display(overall.get('difference'))}，变化率 {display(overall.get('change_rate'), True)}。证据：[总体指标](tables/overall.csv)、[结构化指标](metrics.json)。", ""]
        if overall.get("difference") is None:
            text += ["净销售额不足以计算，不判断总体是否下降，也不计算下降贡献。", ""]
        elif overall["difference"] >= 0:
            text += ["本期净销售额没有下降，不生成下降贡献归因。", ""]
        text += ["| 指标 | 对比期 | 分析期 |", "|---|---:|---:|"]
        for key, name in (("order_count", "订单量（单）"), ("aov", "净客单价（元）"), ("refund_rate", "金额退款率"), ("pre_refund_sales", "退款前销售额（元）"), ("refund_amount", "退款金额（元）")):
            text.append(f"| {name} | {display(overall['control'].get(key), key == 'refund_rate')} | {display(overall['analysis'].get(key), key == 'refund_rate')} |")
        for dim, label in (("channel", "渠道"), ("category", "品类")):
            groups = metrics.get("by_" + dim, {})
            text += ["", f"## {label}变化", ""]
            if not groups:
                text += [f"{label}维度不可计算，原因见上文或质量报告。"]
                continue
            text += [f"证据：[分组表](tables/by_{dim}.csv)、[metrics.json](metrics.json) 的 by_{dim}。", "", "| 分组 | 对比期 | 分析期 | 差额 | 下降贡献 |", "|---|---:|---:|---:|---:|"]
            for key, value in sorted(groups.items(), key=lambda item: (item[1].get("difference") is None, item[1].get("difference") or 0)):
                text.append(f"| {md(key)} | {display(value.get('control_net_sales'))} | {display(value.get('analysis_net_sales'))} | {display(value.get('difference'))} | {display(value.get('contribution'), True)} |")
        text += ["", "## 渠道访客变化", "", "跨天汇总为访客人次。订单数/访客人次不是用户级转化率。", ""]
        if metrics.get("traffic_by_channel"):
            text += ["证据：[访客表](tables/traffic.csv)。", "", "| 渠道 | 对比期人次 | 分析期人次 | 差额 |", "|---|---:|---:|---:|"]
            for key, value in metrics["traffic_by_channel"].items():
                text.append(f"| {md(key)} | {value['control_visits']} | {value['analysis_visits']} | {value['difference']} |")
        else:
            text.append("访客指标不可计算；销售指标可独立保留时已继续计算。")
        text += ["", "## 指标变化的独立核算视角", "", "以下是数学分解，不是业务因果效应。净客单价已包含退款；退款桥接与订单量—净客单价分解是不同视角，不能将两套分解项相加。展示金额四舍五入到分，分解项展示和可能存在 0.01 元舍入差，校验使用未提前舍入的值。", ""]
        text += _decomposition(overall, "总体")
        for dim in ("channel", "category"):
            top = metrics.get("top_decline_source", {}).get(dim)
            if top and top in metrics.get("by_" + dim, {}):
                text += _decomposition(metrics["by_" + dim][top], f"最大下降{'渠道' if dim == 'channel' else '品类'}：{top}")
    if quality:
        unmatched = quality.get("unmatched_product_rows")
        unknown_text = "未知商品行数不可判定" if unmatched is None else f"未知商品 {unmatched} 行"
        text += ["", "## 数据质量", "", f"原始 {quality.get('raw_item_rows', '未知')} 行，清洗后 {quality.get('clean_item_rows', '未知')} 行。完全重复编号 {quality.get('exact_duplicate_item_ids', '未知')} 个，冲突编号 {quality.get('conflicting_item_ids_excluded', '未知')} 个（隔离 {quality.get('conflict_rows_excluded', '未知')} 行），{unknown_text}。冲突原始行净额 {display(quality.get('excluded_raw_net_sales'))} 元，不代表已确认交易损失。", ""]
        if quality.get("bridge"):
            text += ["| 时期 | 原始行净额 | 重复移除 | 去重后冲突移除 | 清洗净额 |", "|---|---:|---:|---:|---:|"]
            for row in quality["bridge"]:
                text.append("| " + " | ".join(md(row.get(k)) if k == "period" else display(row.get(k)) for k in ("period", "raw_net_sales", "duplicate_removed", "conflict_removed_after_dedup", "clean_net_sales")) + " |")
        text += ["", "证据：[质量报告](quality_report.json)、[隔离明细](prepared/quarantined_items.csv)。"]
        impact = quality.get("attribution_impact", {})
        if impact.get("available"):
            text += ["", "### 清洗是否改变下降来源判断", "", f"原始行汇总差额 {display(impact.get('raw_difference'))} 元；清洗后差额 {display(impact.get('clean_difference'))} 元；差额调整 {display(impact.get('difference_adjustment'))} 元。原始行汇总包含异常，不代表真实交易额。", "", "| 视角 | 原始行汇总的最大下降组 | 清洗后最大下降组 | 最大下降组改变 | 排名改变 |", "|---|---|---|---|---|"]
            for dim, label in (("channel", "渠道"), ("category", "品类")):
                value = impact.get("dimensions", {}).get(dim, {})
                if value.get("available"):
                    raw_tops = value.get("raw_tied_sources") or [value.get("raw_top_decline_source") or "无下降"]
                    clean_tops = value.get("clean_tied_sources") or [value.get("clean_top_decline_source") or "无下降"]
                    text.append(f"| {label} | {md('、'.join(raw_tops))} | {md('、'.join(clean_tops))} | {'是' if value.get('source_changed') else '否'} | {'是' if value.get('rank_changed') else '否'} |")
            text += ["", md(impact.get("note", "结论仅限本次清洗前后对照，不代表所有数据扰动下稳定。")), "", "证据：[清洗归因影响](attribution_impact.json)。"]
            unknown = impact.get("unknown_category", {})
            if unknown.get("note"):
                text += ["", "未知品类限制：" + md(unknown["note"])]
    text += ["", "## Agent 选择的指标结论", "", "新运行只接受受支持的结构化指标结论。数值与表述由程序从指标生成并记录具体字段；这不等于验证任意自然语言，也不证明业务因果。"]
    for finding in findings or []:
        links = "、".join(f"[{md(ref)}](evidence_index.json)" for ref in finding.get("evidence_ids", []))
        if finding.get("verification") == "typed_metric_check" and finding.get("claim") and metrics:
            from .claims import validate_findings
            checked = validate_findings([finding["claim"]], metrics, {key: {} for key in finding.get("evidence_ids", [])})
            if checked["passed"]:
                canonical = checked["findings"][0]
                text += ["", f"- {canonical['statement']}（证据：{links}；字段：{md('、'.join(canonical['metric_paths']))}）"]
            else:
                text += ["", "- 该结构化结论未通过当前核对，未作为已验证结论展示。"]
        else:
            text += ["", f"- **历史模型原文，未经语义验证**：{md(finding.get('statement', ''))}（证据引用：{links}）"]
    index_path = output / "figure_index.json"
    if index_path.exists():
        for figure in json.loads(index_path.read_text(encoding="utf-8")).get("figures", []):
            text += ["", f"![{figure['id']}]({figure['png']})", "", figure["caption"]]
    else:
        for name in ("daily_net_sales", "channel_delta", "category_delta"):
            if (output / f"charts/{name}.png").exists():
                text += ["", f"![{name}](charts/{name}.png)"]
    text += ["", "## 边界与过程", "", "渠道与品类是独立拆解视角，贡献不能相加；增长组贡献可为负，下降组可超过 100%。品类订单数不可相加。缺少广告、库存、竞品和用户级数据，指标变化不证明业务因果。", "", f"模型请求 {usage.get('model_calls', 0)} 次，工具调用 {usage.get('tool_calls', 0)} 次，错误 {usage.get('tool_errors', 0)} 次。详见 [调用轨迹](trace.jsonl)、[用量](usage.json)、[校验](validation.json)。"]
    if errors:
        text += ["", "错误与限制（含已恢复历史错误）：", ""] + [f"- {md(error)}" for error in errors]
    (output / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
