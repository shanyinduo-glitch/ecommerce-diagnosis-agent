"""Versioned checkpoints at committed tool boundaries; no credentials are persisted."""
from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import messages_from_dict, messages_to_dict

from .io import dump, sha

VERSION = 1
BUDGET_KEYS = {"max_calls", "max_tool_calls", "max_errors", "max_seconds", "timeout_seconds"}


def save(run, messages, pending_calls):
    dump(run.output / "state.json", {
        "checkpoint_version": VERSION,
        "run_id": run.run_id,
        "folder": str(run.folder),
        "manifest": run.manifest,
        "plan": run.plan,
        "completed": sorted(run.done),
        "usage": run.usage,
        "errors": run.errors,
        "findings": run.findings,
        "inspected": run.inspected,
        "prepared": run.sql is not None,
        "sql_success": run.sql_success,
        "charts_done": run.charts_done,
        "finished": run.finished,
        "inject_sql_error": run.inject_sql_error,
        "injected": run.injected,
        "evidence": run.evidence,
        "messages": messages_to_dict(messages),
        "pending_calls": pending_calls,
        "request_in_flight": run.request_in_flight,
        "tool_in_flight": run.tool_in_flight,
    })


def read(path: Path, config: dict):
    path = path.resolve()
    state = json.loads((path / "state.json").read_text(encoding="utf-8"))
    if state.get("checkpoint_version") != VERSION:
        raise ValueError("旧状态仅支持审计，不能作为可续跑检查点")
    original = state["manifest"]["config"]
    if {k: v for k, v in original.items() if k not in BUDGET_KEYS} != {
        k: v for k, v in config.items() if k not in BUDGET_KEYS
    }:
        raise ValueError("续跑模型、时期或分析配置已变化，请新建运行")
    folder = Path(state["folder"])
    hashes = {name: sha(folder / (name + ".csv")) if (folder / (name + ".csv")).exists() else None
              for name in state["manifest"]["data_hashes"]}
    if hashes != state["manifest"]["data_hashes"]:
        raise ValueError("续跑输入数据哈希不一致")
    current = {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")}
    if current != state["manifest"]["source_code_hashes"]:
        raise ValueError("续跑源码版本已变化，请新建运行")
    for item in state["evidence"].values():
        artifact = (path / item["path"]).resolve()
        if not artifact.is_relative_to(path) or not artifact.is_file() or sha(artifact) != item["sha256"]:
            raise ValueError("续跑证据缺失或哈希不一致")
    return state, messages_from_dict(state["messages"])


def acquire(output):
    """OS lock is released even on process termination; stale filenames are harmless."""
    import os
    handle = (output / ".run.lock").open("a+b")
    if handle.tell() == 0:
        handle.write(b"0"); handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError("该运行正在被另一个进程执行，请勿并发续跑")
    return handle
