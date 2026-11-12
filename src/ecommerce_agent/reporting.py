from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def charts(metrics, output: Path, config):
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    dest = output / "charts"
    dest.mkdir(exist_ok=True)
    daily = pd.read_csv(output / "tables/daily.csv")
    fig, ax = plt.subplots(figsize=(10, 4.5), layout="constrained")
    ax.plot(pd.to_datetime(daily.date), daily.net_sales, marker="o", color="#2563eb")
    ax.axvline(pd.Timestamp(config["analysis_start"]), color="#db2777", linestyle="--", label="分析期开始")
    ax.set(title="每日净销售额（清洗后）", xlabel="订单日期", ylabel="净销售额（元）")
    ax.legend(); ax.grid(alpha=.2); fig.autofmt_xdate()
    fig.savefig(dest / "daily_net_sales.png", dpi=150); plt.close(fig)
    for dim, title in (("channel", "渠道"), ("category", "品类")):
        values = sorted(metrics["by_"+dim].items(), key=lambda x:x[1]["difference"])
        fig, ax = plt.subplots(figsize=(10, 4.5), layout="constrained")
        ax.barh([k for k,v in values], [v["difference"] for k,v in values], color=["#dc2626" if v["difference"]<0 else "#059669" for k,v in values])
        ax.axvline(0, color="#64748b", linewidth=.8)
        ax.set(title=f"{title}净销售额变化\n{config['analysis_start']}—{config['analysis_end']} 减 {config['control_start']}—{config['control_end']}", xlabel="净销售额差额（元）")
        fig.savefig(dest / f"{dim}_delta.png", dpi=150); plt.close(fig)
    return ["charts/daily_net_sales.png", "charts/channel_delta.png", "charts/category_delta.png"]

def display(v, percent=False):
    if v is None:
        return "不可计算（分母为零或无适用口径）"
    return f"{v:.2%}" if percent else f"{v:,.2f}"

def report(output, metrics, quality, findings, status, errors, config, usage):
    text = ["# 电商销售变化诊断报告", "", f"运行状态：**{status}**。", "",
            f"对比期：{config['control_start']} 至 {config['control_end']}；分析期：{config['analysis_start']} 至 {config['analysis_end']}。金额单位为元。", ""]
    if metrics:
        overall = metrics["overall"]
        text += ["## 总体变化", "", f"净销售额由 {display(overall['control']['net_sales'])} 变为 {display(overall['analysis']['net_sales'])}，差额 {display(overall['difference'])}，变化率 {display(overall['change_rate'], True)}。证据：[总体指标](tables/overall.csv)、[结构化指标](metrics.json)。", ""]
        if overall["difference"] >= 0:
            text += ["本期净销售额没有下降，不生成下降贡献归因。", ""]
        text += ["| 指标 | 对比期 | 分析期 |", "|---|---:|---:|"]
        for key, name in (("order_count","订单量"),("aov","客单价"),("refund_rate","退款率")):
            text.append(f"| {name} | {display(overall['control'][key],key=='refund_rate')} | {display(overall['analysis'][key],key=='refund_rate')} |")
        for dim, label in (("channel","渠道"),("category","品类")):
            text += ["", f"## {label}变化", "", f"证据：[分组表](tables/by_{dim}.csv)，完整订单量、客单价、退款率见 [metrics.json](metrics.json) 的 by_{dim}。", "", "| 分组 | 对比期 | 分析期 | 差额 | 下降贡献 |", "|---|---:|---:|---:|---:|"]
            for k,v in sorted(metrics["by_"+dim].items(), key=lambda x:x[1]["difference"]):
                text.append(f"| {k} | {display(v['control_net_sales'])} | {display(v['analysis_net_sales'])} | {display(v['difference'])} | {display(v['contribution'],True)} |")
        text += ["", "## 渠道访客变化", "", "跨天汇总为访客人次。证据：[访客表](tables/traffic.csv)。", "", "| 渠道 | 对比期人次 | 分析期人次 | 差额 |", "|---|---:|---:|---:|"]
        for k,v in metrics["traffic_by_channel"].items():
            text.append(f"| {k} | {v['control_visits']} | {v['analysis_visits']} | {v['difference']} |")
    if quality:
        text += ["", "## 数据质量", "", f"原始 {quality['raw_item_rows']} 行，清洗后 {quality['clean_item_rows']} 行。完全重复编号 {quality['exact_duplicate_item_ids']} 个，冲突编号 {quality['conflicting_item_ids_excluded']} 个（隔离 {quality['conflict_rows_excluded']} 行），未知商品 {quality['unmatched_product_rows']} 行保留金额。冲突原始行净额 {display(quality['excluded_raw_net_sales'])} 元，不代表已确认交易损失。", "", "| 时期 | 原始行净额 | 重复移除 | 去重后冲突移除 | 清洗净额 |", "|---|---:|---:|---:|---:|"]
        for r in quality["bridge"]:
            text.append("| " + " | ".join(str(r[k]) for k in ("period","raw_net_sales","duplicate_removed","conflict_removed_after_dedup","clean_net_sales")) + " |")
        text += ["", "证据：[质量报告](quality_report.json)、[隔离明细](prepared/quarantined_items.csv)。"]
    text += ["", "## Agent 观察与解释", "", "以下为模型基于工具结果的解释，引用有效性已检查，语义仍需人工复核。净客单价已经扣除退款，退款上升与净客单价下降不能当作两项独立损失相加。‘驱动’仅指账面指标变化；模型提及的政策、物流、质量等业务解释均是待验证假设，现有数据不能确认或排除这些原因。"]
    for f in findings:
        links = "、".join(f"[{ref}](evidence_index.json)" for ref in f["evidence_ids"])
        text += ["", f"- {f['statement']}（证据：{links}）"]
    for name in ("daily_net_sales", "channel_delta", "category_delta"):
        if (output / f"charts/{name}.png").exists():
            text += ["", f"![{name}](charts/{name}.png)"]
    text += ["", "## 边界与过程", "", "渠道与品类是独立拆解视角，贡献不能相加；增长组贡献可为负，下降组可超过 100%。品类订单数不可相加。缺少广告、库存、竞品和用户级数据，指标变化不证明业务因果。", "", f"模型请求 {usage['model_calls']} 次，工具调用 {usage['tool_calls']} 次，错误 {usage['tool_errors']} 次。详见 [调用轨迹](trace.jsonl)、[用量](usage.json)、[校验](validation.json)。"]
    if errors:
        text += ["", "错误与限制（含已恢复历史错误）：", ""] + [f"- {e}" for e in errors]
    (output / "report.md").write_text("\n".join(text)+"\n", encoding="utf-8")
