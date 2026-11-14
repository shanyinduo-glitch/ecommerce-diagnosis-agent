"""Free-hosted team demo: immutable real runs plus opt-in, access-gated live jobs."""
from pathlib import Path
import hmac
import json
import os
import sys
import tomllib

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd
import streamlit as st
from webapp import service

st.set_page_config(page_title="电商销售诊断 Agent", page_icon=":material/analytics:", layout="wide")


def secret(name, default=""):
    try:
        return st.secrets.get(name, os.getenv(name, default))
    except (FileNotFoundError, KeyError):
        return os.getenv(name, default)


@st.cache_data(max_entries=32, ttl=30)
def load_json(path, changed):
    return service.read_json(Path(path), {})


def read(path):
    path = Path(path)
    return load_json(str(path), path.stat().st_mtime_ns) if path.exists() else {}


def number(value, percent=False):
    if value is None:
        return "不可计算"
    return f"{value:.2%}" if percent else f"{value:,.2f}"


@st.cache_data(max_entries=8, ttl=60)
def bundle(path, modified):
    return service.download_zip(Path(path))


def result_view(path, historical):
    metrics = read(path / "metrics.json")
    if not metrics:
        st.info("指标尚未生成；可以先查看执行过程。")
        return
    manifest = read(path / "manifest.json")
    overall = metrics["overall"]
    config = manifest.get("config", {})
    usage = read(path / "usage.json")
    st.caption(f"{'历史真实运行' if historical else '当前运行'} · {config.get('model', '未知模型')} · {manifest.get('run_id', path.name)}")
    st.caption(f"对比期 {config.get('control_start', '')}—{config.get('control_end', '')}　/　分析期 {config.get('analysis_start', '')}—{config.get('analysis_end', '')}　金额单位：元")
    with st.container(horizontal=True):
        st.metric("分析期净销售额", number(overall["analysis"].get("net_sales")),
                  delta=number(overall.get("difference")), border=True)
        st.metric("净销售额变化率", number(overall.get("change_rate"), True), border=True)
        st.metric("分析期订单量", str(overall["analysis"].get("order_count", "不可计算")), border=True)
        st.metric("分析期金额退款率", number(overall["analysis"].get("refund_rate"), True), border=True)
    availability = metrics.get("availability", {})
    if not availability.get("full", True):
        st.warning("部分指标不可计算：" + "；".join(availability.get("reasons", [])))
    section = st.segmented_control("分析视角", ["概览与结论", "渠道与品类", "数据质量", "图表素材", "过程与证据", "下载"],
                                   default="概览与结论", key=f"section_{path.name}")
    if section == "概览与结论":
        if overall.get("difference") is not None and overall["difference"] >= 0:
            st.info("本期净销售额没有下降，不生成下降贡献归因。")
        findings = read(path / "findings.json")
        if isinstance(findings, list):
            for item in findings:
                if item.get("verification") == "typed_metric_check":
                    from ecommerce_agent.claims import validate_findings
                    checked = validate_findings([item["claim"]], metrics, read(path / "evidence_index.json"))
                    if checked["passed"]:
                        st.markdown("• " + checked["findings"][0]["statement"])
        st.caption("以上是已核对的指标事实。渠道与品类是不同视角，贡献不能相加；指标变化不能证明具体业务因果。")
        daily = path / "tables/daily.csv"
        if daily.exists():
            frame = pd.read_csv(daily)
            if not frame.empty:
                st.line_chart(frame, x="date", y="net_sales", x_label="订单日期", y_label="净销售额（元）", alt="清洗后每日净销售额趋势")
        bridge = overall.get("decomposition", {}).get("refund_bridge", {})
        if bridge.get("available"):
            st.subheader("退款账面桥接")
            st.write(f"退款前销售额变化 {number(bridge['pre_refund_change'])} 元 − 退款金额变化 {number(bridge['refund_change'])} 元 = 净销售额变化 {number(bridge['net_change'])} 元。")
            st.caption("这是账面恒等关系；退款已经包含在净客单价中，不能重复相加。")
    elif section == "渠道与品类":
        for dim, label in (("channel", "渠道"), ("category", "品类")):
            st.subheader(label)
            file = path / f"tables/by_{dim}.csv"
            if file.exists():
                frame = pd.read_csv(file)
                st.dataframe(frame, hide_index=True, alt=f"{label}两期净销售额、差额和下降贡献")
                if not frame.empty and frame["difference"].notna().any():
                    st.bar_chart(frame, x="group", y="difference", x_label=label, y_label="净销售额差额（元）", alt=f"{label}净销售额变化")
        visits = path / "tables/traffic.csv"
        if visits.exists():
            st.subheader("渠道访客人次")
            st.dataframe(pd.read_csv(visits), hide_index=True, alt="两期渠道访客人次对比")
    elif section == "数据质量":
        quality = metrics.get("data_quality", {})
        with st.container(horizontal=True):
            for label, key in (("原始明细行", "raw_item_rows"), ("重复移除行", "duplicate_rows_removed"), ("冲突隔离行", "conflict_rows_excluded"), ("清洗后明细行", "clean_item_rows")):
                st.metric(label, str(quality.get(key, "不可计算")), border=True)
        st.dataframe(pd.DataFrame(quality.get("bridge", [])), hide_index=True, alt="清洗前后净销售额影响桥接")
        st.caption("冲突排除金额是原始记录汇总，不代表已确认的真实交易损失。未知商品保留金额。")
        impact = path / "tables/attribution_impact.csv"
        if impact.exists():
            st.subheader("清洗前后的分组差额")
            st.dataframe(pd.read_csv(impact), hide_index=True, alt="清洗前后渠道与品类下降来源对比")
    elif section == "图表素材":
        for figure in read(path / "figure_index.json").get("figures", []):
            st.image(str(service.artifact(path, figure["png"])), caption=figure["caption"], width="stretch", alt=figure["caption"])
        st.caption("PNG 为 300 dpi，完整证据包还提供 SVG。请保留时期、单位与图注。")
    elif section == "过程与证据":
        trace_view(path)
        index = read(path / "evidence_index.json")
        if index:
            choice = st.selectbox("选择证据", list(index), key=f"evidence_{path.name}")
            item = index[choice]
            file = service.artifact(path, item["path"])
            st.caption(f"证据 ID：{choice} · 文件：{item['path']}")
            if file.suffix == ".csv":
                st.dataframe(pd.read_csv(file).head(200), hide_index=True, alt="证据表前200行")
            elif file.suffix == ".png":
                st.image(str(file), alt="已登记证据图像")
            else:
                st.code(file.read_text(encoding="utf-8")[:60000], language="json")
        queries = sorted((path / "code").glob("*.sql"))
        if queries:
            query = st.selectbox("查看实际执行 SQL", queries, format_func=lambda p: p.name, key=f"sql_{path.name}")
            st.code(query.read_text(encoding="utf-8"), language="sql")
    elif section == "下载":
        report = path / "report.md"
        if report.exists():
            st.download_button("下载 Markdown 分析报告", report.read_bytes(), file_name=f"{path.name}_report.md", mime="text/markdown")
        st.download_button("下载报告、表格、图表与执行证据", bundle(str(path), (path / "metrics.json").stat().st_mtime_ns),
                           file_name=f"{path.name}_evidence.zip", mime="application/zip")
        st.caption("完整 ZIP 包含报告引用的相对路径文件。运行时临时任务可能在托管重启后丢失，请及时下载。")
    st.caption(f"模型请求 {usage.get('model_calls', '—')} 次 · 工具错误 {usage.get('tool_errors', '—')} 次 · 已记录 Token {usage.get('total_tokens', '—')} · 耗时 {usage.get('seconds', '—')} 秒")


def trace_view(path):
    events = service.trace_events(path)
    rows = [{"工具": e["name"], "任务": e.get("input", {}).get("task_id", "计划更新"),
             "结果": "通过" if e["output"]["ok"] else "错误反馈", "秒": round(e.get("seconds", 0), 3)}
            for e in events if e.get("event") == "tool_result"]
    st.dataframe(pd.DataFrame(rows), hide_index=True, alt="最近工具调用、任务与成功失败状态")
    for event in events[-8:]:
        if event.get("event") == "tool_result" and not event["output"]["ok"]:
            st.warning(event["output"].get("error", "工具错误")[:1000])


st.title("电商销售诊断 Agent")
st.caption("从销售变化到可核查的证据 · Qwen 规划 · SQL 下钻 · 口径校验")
with st.sidebar:
    st.subheader("分析工作台")
    mode = st.segmented_control("使用方式", ["浏览已完成案例", "实时分析"], default="浏览已完成案例", key="mode")
    st.divider()
    st.caption("当前数据为课程公开模拟电商数据。已完成案例来自真实 API 运行，不会再次调用模型。")
    st.caption("免费托管可能休眠；实时任务及上传文件不保证在重启后保留。")

if mode == "浏览已完成案例":
    case = st.pills("选择案例", service.CASES, default="case_01", key="case")
    path = ROOT / "demo_runs" / (case or "case_01")
    st.info("正在浏览已完成的真实运行，可查看过程和下载证据；这不是新发起的实时分析。", icon=":material/history:")
    if path.exists():
        result_view(path, True)
    else:
        st.error("演示证据包缺失，请先运行 scripts/prepare_web_demo.py。")
else:
    api_key = str(secret("DASHSCOPE_API_KEY"))
    expected_code = str(secret("APP_ACCESS_CODE"))
    enabled = str(secret("ENABLE_LIVE_ANALYSIS", "false")).lower() in {"true", "1"}
    supplied_code = st.text_input("组内访问码", type="password", key="access_code", help="用于限制共享模型额度的使用；不要填写模型 API 密钥。")
    allowed = bool(enabled and api_key and expected_code and hmac.compare_digest(supplied_code.encode(), expected_code.encode()))
    if not enabled or not api_key or not expected_code:
        st.info("实时分析尚未由部署者启用。仍可浏览全部历史案例；部署者配置模型密钥、组内访问码并启用实时分析后可运行。")
    elif not allowed:
        st.info("输入组内访问码后可以发起新分析。")
    with st.form("new_run"):
        dataset = st.selectbox("数据来源", [*service.CASES, "uploaded"], format_func=lambda x: "上传三张 CSV" if x == "uploaded" else x)
        st.caption("选择公开案例时忽略上传文件。自有数据的样例及汇总可能发送至 Qwen，请使用获授权的数据。")
        files = {name: st.file_uploader(f"{name}.csv", type=["csv"], key=f"upload_{name}") for name in ("order_items", "products", "traffic")}
        calls = st.slider("本次最多模型请求次数", min_value=10, max_value=20, value=20)
        started = st.form_submit_button("开始实时分析", type="primary", disabled=not allowed, icon=":material/play_arrow:")
    if started:
        config = tomllib.loads((ROOT / "configs/default.toml").read_text(encoding="utf-8"))
        config["max_calls"] = calls
        try:
            payload = {name: file.getvalue() for name, file in files.items() if file is not None} if dataset == "uploaded" else {}
            st.session_state["job_id"] = service.start(dataset, payload, api_key, config)
            st.rerun()
        except (ValueError, OSError) as exc:
            st.error(str(exc))
    with st.expander("恢复已有任务"):
        restore_code = st.text_input("任务恢复码", key="restore_code", help="创建任务后请保存恢复码。持有码且通过组内验证的人可访问对应任务，请勿公开分享。")
        if st.button("打开任务", disabled=not allowed):
            try:
                service.status(restore_code)
                st.session_state["job_id"] = restore_code
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if allowed and st.session_state.get("job_id"):
        job_id = st.session_state["job_id"]
        try:
            current = service.status(job_id)
            st.caption("任务恢复码（请保存，浏览器刷新后可重新打开）：")
            st.code(job_id, language=None)
            @st.fragment(run_every=2 if current["status"] == "running" else None)
            def progress():
                value = service.status(job_id)
                st.write("任务状态：" + value["status"])
                folder = service.run_path(value)
                if folder:
                    snapshot = read(folder / "state.json")
                    completed = set(snapshot.get("completed", []))
                    st.dataframe(pd.DataFrame([{"任务": t["id"], "目标": t["objective"], "状态": "已完成" if t["id"] in completed else "待执行"} for t in snapshot.get("plan", [])]), hide_index=True, alt="当前真实任务计划与完成状态")
                    trace_view(folder)
                if value["status"] == "running":
                    if st.button("请求停止", key="stop_job"):
                        service.stop(job_id)
                        st.info("已请求停止，等待当前模型请求或工具返回后保存检查点。")
                elif value["status"] != current["status"]:
                    st.rerun()
            progress()
            if current["status"] in {"partial", "stopped", "interrupted"}:
                if st.button("续跑：追加最多 10 次请求与 300 秒", key="resume_job"):
                    try:
                        service.resume(job_id, api_key)
                        st.rerun()
                    except (ValueError, OSError) as exc:
                        st.error(str(exc))
            if current["status"] != "running" and service.run_path(current):
                result_view(service.run_path(current), False)
            if current.get("error"):
                st.error("后台执行未完成：" + current["error"])
        except ValueError as exc:
            st.warning(str(exc))
