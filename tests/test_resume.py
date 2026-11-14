import json
import shutil
import tomllib

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from ecommerce_agent.agent import AgentRun
from ecommerce_agent.io import ROOT


@pytest.fixture
def config():
    return tomllib.loads((ROOT / "configs/default.toml").read_text(encoding="utf-8"))


class ScriptedModel:
    """A deterministic tool-calling peer used only for interruption tests, never API scoring."""
    def __init__(self):
        self.index = 0
        self.metrics = None

    def bind_tools(self, *args, **kwargs):
        return self

    def invoke(self, messages):
        for message in messages:
            if isinstance(message, ToolMessage):
                payload = json.loads(message.content)
                if payload.get("ok") and "overall" in payload.get("result", {}):
                    self.metrics = payload["result"]
        tasks = [
            {"id": "inspect", "objective": "inspect", "depends_on": []},
            {"id": "prepare", "objective": "clean", "depends_on": ["inspect"]},
            {"id": "metrics", "objective": "compute", "depends_on": ["prepare"]},
            {"id": "sql", "objective": "query", "depends_on": ["metrics"]},
            {"id": "charts", "objective": "plot", "depends_on": ["metrics"]},
            {"id": "finish", "objective": "report", "depends_on": ["sql", "charts"]},
        ]
        choices = [
            ("update_plan", {"tasks": tasks, "reason": "test"}),
            ("inspect_data", {"task_id": "inspect"}),
            ("prepare_data", {"task_id": "prepare"}),
            ("compute_metrics", {"task_id": "metrics"}),
            ("run_sql", {"task_id": "sql", "query": "SELECT period, COUNT(DISTINCT order_id) AS orders FROM items WHERE period != 'outside' GROUP BY period", "purpose": "period orders"}),
            ("create_charts", {"task_id": "charts"}),
        ]
        if self.index < len(choices):
            name, args = choices[self.index]
        else:
            metric = "net_sales" if self.metrics["overall"]["difference"] is not None else "order_count"
            findings = [{"kind": "metric_comparison", "scope": "overall", "metric": metric, "evidence_ids": ["metrics"]}]
            findings += [{"kind": "decline_source", "scope": dim, "group": self.metrics["top_decline_source"][dim], "evidence_ids": ["metrics"]} for dim in ("channel", "category") if self.metrics["top_decline_source"][dim]]
            name, args = "finish", {"task_id": "finish", "findings": findings}
        self.index += 1
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{self.index}"}], usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})


def test_resume_keeps_evidence_history_and_cumulative_budget(tmp_path, config, monkeypatch):
    import ecommerce_agent.agent as module
    model = ScriptedModel()
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-placeholder")
    monkeypatch.setattr(module, "ChatOpenAI", lambda **kwargs: model)
    config["max_calls"] = 4
    run = AgentRun(ROOT / "data/raw/public/case_01", config, output_root=tmp_path)
    assert run.run()["status"] == "partial"
    saved = json.loads((run.output / "state.json").read_text(encoding="utf-8"))
    assert "metrics" in saved["completed"]
    assert saved["messages"][-1]["type"] == "tool"
    prior_trace = (run.output / "trace.jsonl").read_text(encoding="utf-8")
    config = {**config, "max_calls": 8}
    resumed = AgentRun.resume(run.output, config)
    result = resumed.run()
    assert result["status"] == "completed"
    assert result["model_calls"] == 7
    assert result["total_tokens"] == 84
    assert resumed.output == run.output
    assert (run.output / "trace.jsonl").read_text(encoding="utf-8").startswith(prior_trace)
    assert sum('"name": "prepare_data"' in line and '"event": "tool_result"' in line for line in (run.output / "trace.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    completed = AgentRun.resume(run.output, config)
    completed.usage["status"] = "partial"  # stale cached status must not override completion
    repeated = completed.run()
    assert repeated["status"] == "completed" and repeated["model_calls"] == 7


def test_resume_rejects_changed_input_or_model(tmp_path, config, monkeypatch):
    import ecommerce_agent.agent as module
    folder = tmp_path / "input"
    shutil.copytree(ROOT / "data/raw/public/case_01", folder)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-placeholder")
    monkeypatch.setattr(module, "ChatOpenAI", lambda **kwargs: ScriptedModel())
    config["max_calls"] = 1
    run = AgentRun(folder, config, output_root=tmp_path / "runs")
    run.run()
    with pytest.raises(ValueError, match="配置"):
        AgentRun.resume(run.output, {**config, "model": "changed"})
    with (folder / "traffic.csv").open("a", encoding="utf-8") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="数据哈希"):
        AgentRun.resume(run.output, config)


def test_resume_rejects_changed_evidence(tmp_path, config, monkeypatch):
    import ecommerce_agent.agent as module
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-placeholder")
    model = ScriptedModel()
    monkeypatch.setattr(module, "ChatOpenAI", lambda **kwargs: model)
    config["max_calls"] = 2
    run = AgentRun(ROOT / "data/raw/public/case_01", config, output_root=tmp_path)
    run.run()
    (run.output / "data_profile.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="证据"):
        AgentRun.resume(run.output, config)


def test_interrupted_request_is_not_hidden_from_usage(tmp_path, config, monkeypatch):
    import ecommerce_agent.agent as module
    model = ScriptedModel()
    original = model.invoke
    interrupted = False
    def invoke(messages):
        nonlocal interrupted
        if model.index == 4 and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return original(messages)
    model.invoke = invoke
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-placeholder")
    monkeypatch.setattr(module, "ChatOpenAI", lambda **kwargs: model)
    run = AgentRun(ROOT / "data/raw/public/case_01", config, output_root=tmp_path)
    with pytest.raises(KeyboardInterrupt):
        run.run()
    resumed = AgentRun.resume(run.output, config)
    result = resumed.run()
    assert result["status"] == "completed"
    assert result["model_calls"] == 8
    assert result["total_tokens"] == 84
    assert result["usage_missing_calls"] == 1
    assert not result["token_totals_complete"]


def test_concurrent_run_lock(tmp_path):
    from ecommerce_agent.checkpoint import acquire
    handle = acquire(tmp_path)
    try:
        with pytest.raises(ValueError, match="另一个进程"):
            acquire(tmp_path)
    finally:
        handle.close()
    acquire(tmp_path).close()


@pytest.mark.parametrize("defect", ["traffic", "products", "refund_amount", "channel"])
def test_degraded_agent_finishes_available_work_without_false_completion(tmp_path, config, monkeypatch, defect):
    import pandas as pd
    import ecommerce_agent.agent as module
    folder = tmp_path / "input"
    shutil.copytree(ROOT / "data/raw/public/case_01", folder)
    if defect in {"traffic", "products"}:
        (folder / f"{defect}.csv").unlink()
    else:
        target = folder / "order_items.csv"
        data = pd.read_csv(target, dtype=str, keep_default_na=False)
        data.drop(columns=defect).to_csv(target, index=False, encoding="utf-8-sig")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-placeholder")
    model = ScriptedModel()
    monkeypatch.setattr(module, "ChatOpenAI", lambda **kwargs: model)
    run = AgentRun(folder, config, output_root=tmp_path / "runs")
    result = run.run()
    assert result["status"] == "partial"
    assert run.finished and result["tool_errors"] == 0
    assert run.metrics["overall"]["control"]["order_count"] > 0
    assert not run.metrics["availability"]["full"]
    text = (run.output / "report.md").read_text(encoding="utf-8")
    assert "未完成指标与原因" in text
    if defect == "refund_amount":
        assert run.metrics["overall"]["difference"] is None
        assert "不判断总体是否下降" in text
    else:
        assert run.metrics["overall"]["difference"] == -152886.87
