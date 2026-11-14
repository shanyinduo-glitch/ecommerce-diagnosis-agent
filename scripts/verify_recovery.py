"""Real-API recovery experiments; normal stability runs remain separate."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# The editable package installs ecommerce_agent, while evaluation lives at repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ecommerce_agent.io import ROOT, dump, sha
from evaluation.run_suite import load_config
from evaluation.scorer import score_run


COUNTERS = ("model_calls", "tool_calls", "tool_errors", "input_tokens", "output_tokens", "total_tokens", "seconds")


def read(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def events(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def snapshot(run_path: Path, destination: Path):
    """Copy audit evidence before resumed execution mutates cumulative artifacts."""
    destination.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in ("state.json", "manifest.json", "usage.json", "errors.json", "trace.jsonl", "claim_validation.json"):
        source = run_path / name
        if source.exists():
            shutil.copy2(source, destination / name)
            hashes[name] = sha(source)
    state = read(run_path / "state.json", {})
    manifest = read(run_path / "manifest.json", {})
    result = {"run_path": str(run_path), "artifact_hashes": hashes,
              "usage": read(run_path / "usage.json", {}),
              "checkpoint": {key: state.get(key) for key in ("run_id", "completed", "prepared", "finished", "request_in_flight", "tool_in_flight", "pending_calls")},
              "provenance": {key: manifest.get(key) for key in ("run_id", "case_id", "config", "data_hashes", "source_code_hashes", "resume_count", "resume_events")}}
    dump(destination / "snapshot.json", result)
    return result


def assert_check(checks: list, name: str, condition, detail=None):
    checks.append({"name": name, "passed": bool(condition), "detail": detail})


def write_report(destination: Path, result: dict):
    dump(destination / "result.json", result)
    lines = ["# 真实 API 恢复实验", "", f"实验模式：{result['mode']}；验收通过：{result['passed']}。", "",
             "本实验与正常重复运行分开统计，保留失败尝试。续跑使用同一运行目录与累计用量，不能把首段 partial 的调用成本省略。",
             "resume 模式验证受控模型调用预算边界的保存与继续执行；不代表已验证所有硬进程中断场景。", "",
             "| 检查项 | 通过 |", "|---|---|"]
    lines.extend(f"| {item['name']} | {item['passed']} |" for item in result.get("checks", []))
    if result.get("run_path"):
        lines += ["", f"运行证据目录：`{result['run_path']}`。"]
    lines += ["", "## 用量", "", "| 阶段 | 模型调用 | 工具调用 | Token | Token 完整 | 累计秒 |", "|---|---:|---:|---:|---|---:|"]
    for label, usage in result.get("usage", {}).items():
        lines.append(f"| {label} | {usage.get('model_calls')} | {usage.get('tool_calls')} | {usage.get('total_tokens')} | {usage.get('token_totals_complete')} | {usage.get('seconds')} |")
    if result.get("exception_type"):
        lines += ["", f"实验异常类型：{result['exception_type']}。详细工具错误保留在运行轨迹中；本摘要不复制远程异常正文。"]
    (destination / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_resume(config: dict, folder: Path, destination: Path, result: dict, run_class):
    prefix_config = {**config, "max_calls": 4}
    if config["max_calls"] <= prefix_config["max_calls"]:
        raise ValueError("FinalCallBudgetMustExceedFour")
    run = run_class(folder, prefix_config, output_root=destination / "runs")
    result["run_path"] = str(run.output)
    initial = run.run()
    before = snapshot(run.output, destination / "before_resume")
    result["usage"] = {"initial_partial": before["usage"], "total_cumulative": before["usage"]}
    before_trace = (run.output / "trace.jsonl").read_bytes() if (run.output / "trace.jsonl").exists() else b""
    checks = result["checks"]
    assert_check(checks, "initial_status_partial", initial.get("status") == "partial")
    assert_check(checks, "controlled_four_request_boundary", before["usage"].get("model_calls") == 4)
    assert_check(checks, "checkpoint_is_not_finished", before["checkpoint"].get("finished") is False)
    assert_check(checks, "checkpoint_exists", "state.json" in before["artifact_hashes"])
    assert_check(checks, "initial_has_no_api_error", not any(e.get("event") == "model_error" for e in events(run.output / "trace.jsonl")))
    if not all(check["passed"] for check in checks):
        result["stopped_reason"] = "InitialControlledPauseNotEstablished"
        return
    resumed = run_class.resume(run.output, dict(config))
    outcome = resumed.run()
    after = snapshot(run.output, destination / "after_resume")
    final_usage = after["usage"]
    increment = {key: (final_usage[key] - before["usage"][key])
                 for key in COUNTERS if key in final_usage and key in before["usage"]}
    increment["token_totals_complete"] = final_usage.get("token_totals_complete")
    result["usage"].update(resume_increment=increment, total_cumulative=final_usage)
    assert_check(checks, "same_run_and_output", resumed.run_id == run.run_id and Path(outcome["path"]).resolve() == run.output.resolve())
    assert_check(checks, "resume_count_incremented", (after["provenance"].get("resume_count") or 0) == (before["provenance"].get("resume_count") or 0) + 1)
    assert_check(checks, "original_trace_prefix_retained", (run.output / "trace.jsonl").read_bytes().startswith(before_trace))
    assert_check(checks, "checkpoint_changed", before["artifact_hashes"].get("state.json") != after["artifact_hashes"].get("state.json"))
    for key in ("data_hashes", "source_code_hashes"):
        assert_check(checks, f"{key}_unchanged", before["provenance"].get(key) == after["provenance"].get(key))
    assert_check(checks, "cumulative_counters_preserve_initial_cost", all(increment.get(key, -1) >= 0 for key in COUNTERS))
    assert_check(checks, "resume_made_new_requests", increment.get("model_calls", 0) > 0)
    assert_check(checks, "final_status_completed", outcome.get("status") == "completed")


def verify_sql_error(config: dict, folder: Path, destination: Path, result: dict, run_class):
    run = run_class(folder, dict(config), output_root=destination / "runs", inject_sql_error=True)
    result["run_path"] = str(run.output)
    outcome = run.run()
    after = snapshot(run.output, destination / "after_sql_error")
    result["usage"] = {"total_cumulative": after["usage"]}
    attempts = events(run.output / "sql_attempts.jsonl")
    trace = events(run.output / "trace.jsonl")
    sql_trace = [event for event in trace if event.get("event") == "tool_result" and event.get("name") == "run_sql"]
    failed_index = next((index for index, event in enumerate(sql_trace)
                         if event.get("output", {}).get("ok") is False
                         and "injected_missing_column" in event.get("output", {}).get("error", "")), None)
    checks = result["checks"]
    assert_check(checks, "fault_is_explicitly_injected", bool(attempts) and attempts[0].get("injected") is True
                 and "injected_missing_column" in attempts[0].get("executed_query", ""))
    assert_check(checks, "actual_sql_error_returned", failed_index is not None)
    assert_check(checks, "later_sql_tool_succeeded", failed_index is not None and
                 any(event.get("output", {}).get("ok") is True for event in sql_trace[failed_index + 1:]))
    assert_check(checks, "later_query_not_injected", any(attempt.get("injected") is False for attempt in attempts[1:]))
    assert_check(checks, "final_status_completed", outcome.get("status") == "completed")
    result["sql_attempts"] = attempts


def main(argv=None):
    parser = argparse.ArgumentParser(description="真实 Qwen API 恢复验证，会产生模型调用与 Token 用量；失败和首段 partial 成本均保留。")
    parser.add_argument("--mode", required=True, choices=("resume", "sql-error"), help="resume：4 次请求后续跑至原预算；sql-error：受控注入 SQL 错误后恢复")
    parser.add_argument("--case", default="case_01", choices=("case_01", "case_02", "case_03"))
    parser.add_argument("--config", type=Path, default=ROOT / "configs/default.toml", help="最终总预算配置；resume 要求 max_calls 大于 4")
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/recovery")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.mode == "resume" and config["max_calls"] <= 4:
        parser.error("resume 模式要求配置中的 max_calls 大于 4（最终累计预算）")
    destination = args.output_root / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + args.mode.replace("-", "_") + "_" + uuid.uuid4().hex[:6])
    destination.mkdir(parents=True, exist_ok=True)
    result = {"mode": args.mode, "case_id": args.case, "started_utc": datetime.now(timezone.utc).isoformat(),
              "config": config, "passed": False, "checks": [], "usage": {}}
    write_report(destination, result)
    try:
        # Defer model imports until after parsing --help and persisting the protocol.
        from ecommerce_agent.agent import AgentRun
        folder = ROOT / "data/raw/public" / args.case
        if args.mode == "resume":
            verify_resume(config, folder, destination, result, AgentRun)
        else:
            verify_sql_error(config, folder, destination, result, AgentRun)
        if result.get("run_path"):
            path = Path(result["run_path"])
            refs = read(ROOT / "evaluation/references/public_reference_answers.json")
            scored = score_run(path, refs[args.case])
            result["score"] = scored
            assert_check(result["checks"], "all_reference_fields_correct", scored["numeric_accuracy"] == 1)
            assert_check(result["checks"], "decline_groups_correct", scored["channel_hit"] and scored["category_hit"])
            validation = read(path / "claim_validation.json", {})
            assert_check(result["checks"], "typed_claim_validation_passed", validation.get("passed") is True and bool(validation.get("checks")))
        result["passed"] = bool(result["checks"]) and all(check["passed"] for check in result["checks"])
    except Exception as exc:
        result["exception_type"] = type(exc).__name__
        if result.get("run_path"):
            try:
                final_snapshot = snapshot(Path(result["run_path"]), destination / "on_exception")
                result["usage"]["total_cumulative"] = final_snapshot["usage"]
            except (OSError, ValueError):
                pass
    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    write_report(destination, result)
    print(destination)
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
