"""Lifecycle feedback must let an agent distinguish SQL repair from a new query."""
import json
import tomllib

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from ecommerce_agent.agent import AgentRun, model_payload
from ecommerce_agent.io import ROOT


@pytest.fixture
def prepared_run(tmp_path):
    config = tomllib.loads((ROOT / "configs/default.toml").read_text(encoding="utf-8"))
    run = AgentRun(ROOT / "data/raw/public/case_01", config, output_root=tmp_path)
    tools = {tool.name: tool for tool in run.build_tools()}
    tasks = [
        {"id": "inspect", "objective": "检查", "depends_on": []},
        {"id": "prepare", "objective": "清洗", "depends_on": ["inspect"]},
        {"id": "metrics", "objective": "指标", "depends_on": ["prepare"]},
        {"id": "sql", "objective": "查询", "depends_on": ["metrics"]},
        {"id": "charts", "objective": "图表", "depends_on": ["sql"]},
        {"id": "finish", "objective": "报告", "depends_on": ["charts"]},
    ]
    tools["update_plan"].invoke({"tasks": tasks, "reason": "controlled lifecycle test"})
    for name, task in (("inspect_data", "inspect"), ("prepare_data", "prepare"), ("compute_metrics", "metrics")):
        tools[name].invoke({"task_id": task})
    yield run, tools
    if run.sql is not None:
        run.sql.close()


def query(sql):
    return {"task_id": "sql", "query": sql, "purpose": "核对记录数"}


def test_successful_query_consumes_id_and_exposes_next_ready_task(prepared_run):
    run, tools = prepared_run
    assert run.workflow_status()["ready_task_ids"] == ["sql"]
    result = tools["run_sql"].invoke(query("SELECT COUNT(*) AS n FROM items"))
    assert result["rows"] == [{"n": 10236}]
    assert result["completed_task_id"] == "sql" and result["task_status"] == "completed"
    after = run.workflow_status()
    assert after["ready_task_ids"] == ["charts"]
    assert {task["id"]: task["status"] for task in after["tasks"]}["sql"] == "completed"
    assert after["required_evidence"]["sql"] is True
    with pytest.raises(ValueError, match="已完成") as rejected:
        tools["run_sql"].invoke(query("SELECT SUM(net_cents) AS total FROM items"))
    assert "charts" in str(rejected.value)
    assert run.sql_success == 1
    assert len(list((run.output / "code").glob("*.sql"))) == 1


def test_sql_error_keeps_id_ready_for_repair(prepared_run):
    run, tools = prepared_run
    with pytest.raises(ValueError, match="SQL执行失败"):
        tools["run_sql"].invoke(query("SELECT nonexistent_field FROM items"))
    status = run.workflow_status()
    assert status["ready_task_ids"] == ["sql"]
    assert status["required_evidence"]["sql"] is False
    assert "sql" not in run.done
    corrected = tools["run_sql"].invoke(query("SELECT COUNT(*) AS n FROM items"))
    assert corrected["completed_task_id"] == "sql"
    assert run.workflow_status()["ready_task_ids"] == ["charts"]
    assert len(list((run.output / "code").glob("*.sql"))) == 2


def test_unknown_task_is_not_misreported_as_completed(prepared_run):
    run, tools = prepared_run
    with pytest.raises(ValueError, match="未知任务"):
        tools["run_sql"].invoke({**query("SELECT COUNT(*) AS n FROM items"), "task_id": "not_planned"})
    assert run.workflow_status()["ready_task_ids"] == ["sql"]


def test_metric_context_compaction_preserves_workflow(prepared_run):
    run, _ = prepared_run
    workflow = run.workflow_status()
    payload = {"ok": True, "result": run.metrics, "workflow": workflow}
    compact = model_payload("compute_metrics", payload)
    assert compact["workflow"] == workflow
    assert compact["result"]["overall"] == run.metrics["overall"]
    assert "attribution_impact" not in compact["result"]["data_quality"]
    assert "attribution_impact" in run.metrics["data_quality"]


def test_model_receives_authoritative_status_on_failure_and_success(prepared_run, monkeypatch):
    import ecommerce_agent.agent as module
    run, _ = prepared_run
    run.config["max_calls"] = 3
    observed = []

    class RepairingPeer:
        index = 0

        def bind_tools(self, *args, **kwargs):
            return self

        def invoke(self, messages):
            self.index += 1
            if self.index > 1:
                message = next(message for message in reversed(messages) if isinstance(message, ToolMessage))
                payload = json.loads(message.content)
                observed.append(payload)
                if self.index == 2:
                    assert payload["ok"] is False
                    assert payload["workflow"]["ready_task_ids"] == ["sql"]
                    assert payload["workflow"]["remaining_budget"]["model_calls"] == 2
                else:
                    assert payload["ok"] is True
                    assert payload["result"]["completed_task_id"] == "sql"
                    assert payload["workflow"]["ready_task_ids"] == ["charts"]
                    assert payload["workflow"]["remaining_budget"]["model_calls"] == 1
            if self.index == 3:
                return AIMessage(content="test boundary", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
            sql = "SELECT nonexistent_field FROM items" if self.index == 1 else "SELECT COUNT(*) AS n FROM items"
            return AIMessage(content="", tool_calls=[{"name": "run_sql", "args": query(sql), "id": f"repair_{self.index}"}],
                             usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})

    monkeypatch.setenv("DASHSCOPE_API_KEY", "local-test-placeholder")
    monkeypatch.setattr(module, "ChatOpenAI", lambda **kwargs: RepairingPeer())
    result = run.run()
    assert result["status"] == "partial"  # deliberately stop before chart/report work
    assert result["model_calls"] == 3 and result["tool_errors"] == 1
    assert len(observed) == 2 and run.sql_success == 1
