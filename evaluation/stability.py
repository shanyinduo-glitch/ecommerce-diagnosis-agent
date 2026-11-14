"""Audit saved runs without silently dropping failures or pooling unlike experiments."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from ecommerce_agent.io import dump
from evaluation.scorer import score


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def fingerprint(manifest: dict) -> str | None:
    """Missing provenance is unknown, never permission to pool legacy results."""
    required = ("case_id", "config", "data_hashes", "source_code_hashes")
    if any(not manifest.get(key) for key in required):
        return None
    identity = {key: manifest[key] for key in required}
    identity["fault_injection"] = bool(manifest.get("fault_injection", False))
    identity["resumed"] = bool(manifest.get("resumed_from") or manifest.get("resume_count"))
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def claim_counts(report: dict | None) -> dict:
    if report is None:
        return {"available": False, "passed": None, "total": 0, "validated": 0, "coverage": None}
    checks = report.get("checks", [])
    total = report.get("total_claims", report.get("total", len(checks)))
    validated = report.get("validated_claims", report.get("validated", sum(bool(c.get("passed")) for c in checks)))
    return {"available": True, "passed": bool(report.get("passed", False)),
            "total": total, "validated": validated, "coverage": validated / total if total else None}


def trace_counts(path: Path) -> dict:
    """Count observed retries/recoveries; a successful retry is not semantic proof."""
    keys = ("model_error_events", "model_retry_attempts", "tool_retry_attempts", "tool_recoveries")
    if not path.exists():
        return {key: None for key in keys}
    counts = dict.fromkeys(keys, 0)
    pending_tools = set()
    pending_model_error = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event") in ("model_error", "model_response"):
            if pending_model_error:
                counts["model_retry_attempts"] += 1
            pending_model_error = event["event"] == "model_error"
            counts["model_error_events"] += int(pending_model_error)
        if event.get("event") == "tool_result":
            name = event.get("name")
            success = event.get("output", {}).get("ok")
            if name in pending_tools:
                counts["tool_retry_attempts"] += 1
                if success:
                    counts["tool_recoveries"] += 1
            if success:
                pending_tools.discard(name)
            elif success is False:
                pending_tools.add(name)
    return counts


def collect_run(path: Path, expected: dict, *, manifest: dict | None = None,
                execution_error: str | None = None, attempt_id: str | None = None) -> dict:
    """Score all expected fields, even when a run crashed before saving metrics."""
    path = path.resolve()
    manifest = manifest or read_json(path / "manifest.json", {})
    actual = read_json(path / "metrics.json", {})
    scored = score(actual, expected)
    usage = read_json(path / "usage.json", {})
    status = usage.get("status", manifest.get("status", "failed"))
    if execution_error:
        status = "failed"
    usage = {**usage, "status": status}
    for key in ("model_calls", "tool_calls", "tool_errors", "total_tokens", "seconds"):
        usage.setdefault(key, None)
    complete_tokens = usage.get("token_totals_complete")
    if complete_tokens is None:
        complete_tokens = usage.get("usage_missing_calls") == 0 and usage.get("total_tokens") is not None
    usage["token_totals_complete"] = bool(complete_tokens)
    sql_attempts = path / "sql_attempts.jsonl"
    sql_count = sum(bool(line.strip()) for line in sql_attempts.read_text(encoding="utf-8").splitlines()) if sql_attempts.exists() else None
    validation = read_json(path / "claim_validation.json")
    if validation is None:
        validation = read_json(path / "claims_validation.json")
    return {"case_id": manifest.get("case_id", path.parent.name),
            "attempt_id": attempt_id or manifest.get("run_id", path.name), "run_path": str(path),
            "fault_injection": bool(manifest.get("fault_injection", False)),
            "resumed": bool(manifest.get("resumed_from") or manifest.get("resume_count")),
            "identity": fingerprint(manifest), "execution_error": execution_error,
            **scored, "usage": usage, "claims": claim_counts(validation), "sql_attempts": sql_count,
            "recovery": trace_counts(path / "trace.jsonl")}


def resource_stats(values) -> dict:
    values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
    return {"count": len(values), "mean": statistics.mean(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None,
            "stdev": statistics.stdev(values) if len(values) > 1 else None}


def summarize_runs(results: list[dict]) -> list[dict]:
    buckets = defaultdict(list)
    seen = set()
    for row in results:
        # Copied paths or repeated CLI arguments must not manufacture repetitions.
        attempt = row.get("attempt_id") or row["run_path"]
        unique = str(Path(row["run_path"]).resolve())
        if unique in seen:
            raise ValueError("同一运行被重复评分，不能作为独立重复实验")
        seen.add(unique)
        key = (row["case_id"], row["fault_injection"], row.get("resumed", False),
               row.get("identity") or f"unknown:{attempt}:{row['run_path']}")
        buckets[key].append(row)
    groups = []
    for (case, fault, resumed, identity), rows in sorted(buckets.items()):
        statuses = Counter(r["usage"]["status"] for r in rows)
        claims = [r["claims"] for r in rows if r["claims"]["available"]]
        total_claims = sum(c["total"] for c in claims)
        groups.append({
            "case_id": case, "fault_injection": fault, "resumed": resumed,
            "identity": rows[0].get("identity"), "provenance_complete": not identity.startswith("unknown:"),
            "attempts": len(rows), "status_counts": dict(statuses),
            "completed_rate": statuses["completed"] / len(rows),
            "correct_and_completed_rate": sum(r["usage"]["status"] == "completed" and r["numeric_accuracy"] == 1
                                              and r["channel_hit"] and r["category_hit"] for r in rows) / len(rows),
            "numeric_accuracy": resource_stats(r["numeric_accuracy"] for r in rows),
            "numeric_correct": sum(r["numeric_correct"] for r in rows),
            "numeric_total": sum(r["numeric_total"] for r in rows),
            "channel_hit_rate": sum(r["channel_hit"] for r in rows) / len(rows),
            "category_hit_rate": sum(r["category_hit"] for r in rows) / len(rows),
            "claim_validation_runs": len(claims),
            "claim_validation_pass_rate": sum(c["passed"] for c in claims) / len(claims) if claims else None,
            "claim_coverage": sum(c["validated"] for c in claims) / total_claims if total_claims else None,
            "claims_total": total_claims,
            "incomplete_token_runs": sum(not r["usage"]["token_totals_complete"] for r in rows),
            "resources": {key: resource_stats(r["usage"].get(key) for r in rows)
                          for key in ("model_calls", "tool_calls", "tool_errors", "seconds")},
            "recovery": {key: resource_stats(r.get("recovery", {}).get(key) for r in rows)
                         for key in ("model_error_events", "model_retry_attempts", "tool_retry_attempts", "tool_recoveries")},
            "tokens_complete_only": resource_stats(r["usage"].get("total_tokens") for r in rows if r["usage"]["token_totals_complete"]),
            "tokens_recorded_including_partial": resource_stats(r["usage"].get("total_tokens") for r in rows),
            "run_paths": [r["run_path"] for r in rows],
        })
    return groups


def _csv(path: Path, rows: list[dict]):
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_summary(dest: Path, results: list[dict], protocol: dict | None = None):
    dest.mkdir(parents=True, exist_ok=True)
    groups = summarize_runs(results)
    dump(dest / "results.json", results)
    dump(dest / "stability.json", {"protocol": protocol or {}, "groups": groups})
    _csv(dest / "per_run.csv", [{"case_id": r["case_id"], "attempt_id": r["attempt_id"],
        "run_path": r["run_path"], "identity": r["identity"], "fault_injection": r["fault_injection"],
        "resumed": r.get("resumed", False), "numeric_correct": r["numeric_correct"], "numeric_total": r["numeric_total"],
        "channel_hit": r["channel_hit"], "category_hit": r["category_hit"],
        "claim_validation_available": r["claims"]["available"], "claim_validation_passed": r["claims"]["passed"],
        "claim_coverage": r["claims"]["coverage"], "execution_error": r.get("execution_error"),
        **{key: r.get("recovery", {}).get(key) for key in ("model_error_events", "model_retry_attempts", "tool_retry_attempts", "tool_recoveries")},
        **{key: r["usage"].get(key) for key in ("status", "model_calls", "tool_calls", "tool_errors", "total_tokens", "token_totals_complete", "seconds")}}
        for r in results])
    flat_groups = []
    for g in groups:
        row = {key: g[key] for key in ("case_id", "identity", "fault_injection", "resumed", "attempts", "completed_rate", "correct_and_completed_rate", "channel_hit_rate", "category_hit_rate", "claim_validation_runs", "claim_validation_pass_rate", "claim_coverage", "incomplete_token_runs")}
        for name, values in {**g["resources"], **g["recovery"], "numeric_accuracy": g["numeric_accuracy"], "tokens_complete_only": g["tokens_complete_only"]}.items():
            row.update({f"{name}_{key}": value for key, value in values.items()})
        flat_groups.append(row)
    _csv(dest / "stability.csv", flat_groups)
    lines = ["# 公开案例与重复运行评测", "",
        "所有尝试均计入分母，包括 partial 和失败；不挑选最佳运行。数值缺失判错，下降渠道和品类命中不等同于业务因果识别。",
        "同一案例仅在模型配置、输入哈希、源码哈希及运行模式一致时合并；缺少来源信息的运行不合并。正常、故障注入和恢复续跑分别统计。",
        "结论检查覆盖率仅反映声明的受支持结论类型，不代表任意自然语言已获语义证明。Token 不完整的运行单列，不纳入完整 Token 均值。", "",
        "| 案例 | 故障注入 | 状态 | 数值正确 | 渠道命中 | 品类命中 | 模型调用 | 工具错误 | Token | 用量完整 | 秒 |",
        "|---|---|---|---|---|---|---:|---:|---:|---|---:|"]
    for r in results:
        u = r["usage"]
        lines.append(f"| {r['case_id']} | {r['fault_injection']} | {u['status']} | {r['numeric_correct']}/{r['numeric_total']} | {r['channel_hit']} | {r['category_hit']} | {u['model_calls']} | {u['tool_errors']} | {u['total_tokens']} | {u['token_totals_complete']} | {u['seconds']} |")
    lines += ["", "## 分组稳定性", "", "| 案例 | 版本 | 模式 | 次数 | 完成率 | 数值及来源全对且完成 | 结论检查运行数 | 结论检查通过率 |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for g in groups:
        mode = "故障注入" if g["fault_injection"] else "正常"
        if g["resumed"]:
            mode += "／续跑"
        claim_rate = f"{g['claim_validation_pass_rate']:.1%}" if g["claim_validation_pass_rate"] is not None else "未记录"
        lines.append(f"| {g['case_id']} | {(g['identity'] or '来源不完整')[:12]} | {mode} | {g['attempts']} | {g['completed_rate']:.1%} | {g['correct_and_completed_rate']:.1%} | {g['claim_validation_runs']} | {claim_rate} |")
    lines += ["", "## 资源统计", ""]
    for g in groups:
        lines += [f"{g['case_id']}（{(g['identity'] or 'unknown')[:12]}）资源均值与范围（包含失败尝试中已记录的值）：", ""]
        for key, stat in {**g["resources"], **g["recovery"], "tokens_complete_only": g["tokens_complete_only"]}.items():
            if stat["count"]:
                lines.append(f"- {key}: 均值 {stat['mean']:.3f}，范围 {stat['min']}–{stat['max']}，样本数 {stat['count']}。")
        if g["attempts"] < 3:
            lines += ["", "本组少于三次独立运行，仅作为单次/初步验证，不能宣称已充分验证稳定性。"]
    lines += ["", "重试与恢复按实际轨迹统计：同名工具报错后再次调用计为重试，随后返回 ok 计为工具恢复；这不等于分析结论已获语义验证。缺少轨迹时这些计数为未知。"]
    (dest / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return groups
