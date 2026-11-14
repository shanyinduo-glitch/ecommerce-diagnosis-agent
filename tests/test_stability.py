import json
import pytest

from ecommerce_agent.io import ROOT, dump
from evaluation import run_suite
from evaluation.stability import collect_run, fingerprint, resource_stats, summarize_runs, trace_counts, write_summary


@pytest.fixture
def reference():
    return json.loads((ROOT / "evaluation/references/public_reference_answers.json").read_text(encoding="utf-8"))["case_01"]


def saved_run(tmp_path, reference, name="a", *, completed=True, complete_tokens=True, config=None):
    path = tmp_path / name
    manifest = {"case_id": "case_01", "run_id": name, "config": config or {"model": "m"},
                "data_hashes": {"items": "same"}, "source_code_hashes": {"agent.py": "same"},
                "fault_injection": False}
    dump(path / "manifest.json", manifest)
    dump(path / "metrics.json", reference)
    dump(path / "usage.json", {"status": "completed" if completed else "partial", "model_calls": 10,
         "tool_calls": 9, "tool_errors": 1, "seconds": 20, "total_tokens": 100,
         "token_totals_complete": complete_tokens})
    return path, manifest


def test_missing_artifacts_score_all_expected_fields_and_preserve_unknown_usage(tmp_path, reference):
    result = collect_run(tmp_path / "missing", reference, manifest={"case_id": "case_01"}, execution_error="RuntimeError")
    assert result["numeric_total"] == 66 and result["numeric_correct"] == 0
    assert result["usage"]["status"] == "failed"
    assert result["usage"]["total_tokens"] is None
    assert not result["usage"]["token_totals_complete"]
    assert result["claims"]["coverage"] is None


def test_failures_remain_in_denominator_and_incomplete_tokens_excluded(tmp_path, reference):
    a, ma = saved_run(tmp_path, reference)
    b, mb = saved_run(tmp_path, reference, "b", completed=False, complete_tokens=False)
    c, mc = saved_run(tmp_path, reference, "c")
    rows = [collect_run(a, reference), collect_run(b, reference),
            collect_run(c, reference, execution_error="RuntimeError")]
    group, = summarize_runs(rows)
    assert group["attempts"] == 3
    assert group["completed_rate"] == pytest.approx(1 / 3)
    assert group["correct_and_completed_rate"] == pytest.approx(1 / 3)
    assert group["status_counts"] == {"completed": 1, "partial": 1, "failed": 1}
    assert group["incomplete_token_runs"] == 1
    assert group["tokens_complete_only"]["count"] == 2
    assert group["resources"]["model_calls"]["count"] == 3


def test_distinct_models_data_code_fault_and_resume_never_pool(tmp_path, reference):
    path, manifest = saved_run(tmp_path, reference)
    rows = [collect_run(path, reference)]
    variants = [{"config": {"model": "different"}}, {"data_hashes": {"items": "changed"}},
                {"source_code_hashes": {"agent.py": "changed"}}, {"fault_injection": True}, {"resume_count": 1}]
    for index, changes in enumerate(variants):
        target = tmp_path / f"variant_{index}"
        edited = {**manifest, **changes, "run_id": target.name}
        dump(target / "manifest.json", edited)
        rows.append(collect_run(target, reference))
    assert len(summarize_runs(rows)) == 6
    assert fingerprint({"case_id": "case_01"}) is None


def test_missing_identity_and_duplicate_paths_cannot_manufacture_stability(tmp_path, reference):
    rows = [collect_run(tmp_path / name, reference, manifest={"case_id": "case_01", "run_id": name}) for name in ("a", "b")]
    assert len(summarize_runs(rows)) == 2
    assert all(not g["provenance_complete"] for g in summarize_runs(rows))
    duplicate = {**rows[0], "attempt_id": "renamed"}
    with pytest.raises(ValueError, match="重复"):
        summarize_runs([rows[0], duplicate])


def test_claim_results_and_exported_csv(tmp_path, reference):
    path, _ = saved_run(tmp_path, reference)
    dump(path / "claim_validation.json", {"passed": False, "total_claims": 3, "validated_claims": 2})
    result = collect_run(path, reference)
    assert result["claims"]["coverage"] == pytest.approx(2 / 3)
    out = tmp_path / "summary"
    groups = write_summary(out, [result])
    assert groups[0]["claim_validation_pass_rate"] == 0
    assert (out / "per_run.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    assert "不能宣称" in (out / "summary.md").read_text(encoding="utf-8")
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))[0]["numeric_correct"] == 66


def test_resources_do_not_invent_zero_or_single_sample_stdev():
    assert resource_stats([None, float("nan"), True])["count"] == 0
    assert resource_stats([12])["stdev"] is None
    assert resource_stats([10, 20])["mean"] == 15


def test_suite_keeps_constructor_failures_and_continues(tmp_path, monkeypatch, reference):
    project = tmp_path / "project"
    source = project / "src/ecommerce_agent/version.py"
    source.parent.mkdir(parents=True)
    source.write_text("VERSION = 1\n", encoding="utf-8")
    for name in ("order_items", "products", "traffic"):
        data = project / "data/raw/public/case_01" / f"{name}.csv"
        data.parent.mkdir(parents=True, exist_ok=True)
        data.write_text("header\n", encoding="utf-8")
    monkeypatch.setattr(run_suite, "ROOT", project)
    call_count = 0

    class FakeRun:
        def __init__(self, folder, config, inject_sql_error=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("secret-response-must-not-be-persisted")
            self.output = tmp_path / f"run_{call_count}"

        def run(self):
            dump(self.output / "metrics.json", reference)
            dump(self.output / "usage.json", {"status": "completed", "model_calls": 1,
                 "tool_calls": 1, "tool_errors": 0, "seconds": 1, "total_tokens": 10,
                 "token_totals_complete": True})
            return {"path": str(self.output)}

    destination = tmp_path / "suite"
    rows = run_suite.run_experiment(["case_01"], 3, {"model": "m"}, {"case_01": reference},
                                    destination, run_factory=FakeRun)
    assert len(rows) == 3 and rows[0]["usage"]["status"] == "failed"
    group, = summarize_runs(rows)
    assert group["completed_rate"] == pytest.approx(2 / 3)
    protocol = json.loads((destination / "protocol.json").read_text(encoding="utf-8"))
    assert protocol["status"] == "finished" and len(protocol["attempts"]) == 3
    assert "secret-response" not in (destination / "results.json").read_text(encoding="utf-8")


def test_config_environment_override_matches_cli(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('model = "base"\nbase_url = "https://base"\n', encoding="utf-8")
    monkeypatch.setenv("ECOMMERCE_MODEL", "override")
    monkeypatch.setenv("ECOMMERCE_BASE_URL", "https://override")
    assert run_suite.load_config(path) == {"model": "override", "base_url": "https://override"}


def test_retries_and_recoveries_count_actual_trace_events(tmp_path):
    path = tmp_path / "trace.jsonl"
    events = [{"event": "model_error"}, {"event": "model_response"},
              {"event": "tool_result", "name": "run_sql", "output": {"ok": False}},
              {"event": "tool_result", "name": "update_plan", "output": {"ok": True}},
              {"event": "tool_result", "name": "run_sql", "output": {"ok": False}},
              {"event": "tool_result", "name": "run_sql", "output": {"ok": True}}]
    path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    assert trace_counts(path) == {"model_error_events": 1, "model_retry_attempts": 1,
                                  "tool_retry_attempts": 2, "tool_recoveries": 1}
    assert all(value is None for value in trace_counts(tmp_path / "missing").values())
