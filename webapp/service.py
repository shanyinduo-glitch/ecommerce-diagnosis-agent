from __future__ import annotations
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import zipfile

from ecommerce_agent.io import ROOT, dump
from ecommerce_agent.checkpoint import acquire

CASES = ("case_01", "case_02", "case_03")
MAX_FILE_BYTES = 8 * 1024 * 1024


def workspace():
    path = Path(os.environ.get("ECOMMERCE_WEB_ROOT", ROOT / "outputs/web"))
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def job_dir(job_id):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("任务恢复码无效")
    return workspace() / job_id


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


def status(job_id):
    value = read_json(job_dir(job_id) / "job.json")
    if value is None:
        raise ValueError("任务不存在；免费托管重启后临时任务可能已清除")
    if value["status"] == "running" and time.time() - value["heartbeat"] > 90:
        value = {**value, "status": "interrupted"}
    return value


def _launch(path, resume, api_key):
    env = dict(os.environ)
    env["DASHSCOPE_API_KEY"] = api_key
    env["MPLBACKEND"] = "Agg"
    args = [sys.executable, str(ROOT / "webapp/worker.py"), path.name]
    if resume:
        args.append("--resume")
    with (path / "worker.log").open("ab") as log:
        subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def start(case, uploads, api_key, config):
    if case not in (*CASES, "uploaded"):
        raise ValueError("未知案例")
    if case == "uploaded":
        if set(uploads) != {"order_items", "products", "traffic"}:
            raise ValueError("请上传完整三张 CSV")
        if any(not data or len(data) > MAX_FILE_BYTES for data in uploads.values()):
            raise ValueError("每张 CSV 必须非空且不超过 8 MB")
    root = workspace()
    lock = acquire(root)
    try:
        _ensure_idle()
        job_id = uuid.uuid4().hex
        path = job_dir(job_id)
        path.mkdir()
        if case == "uploaded":
            (path / "input").mkdir()
            for name, data in uploads.items():
                (path / "input" / f"{name}.csv").write_bytes(data)
        dump(path / "request.json", {"case": case, "config": config})
        dump(path / "job.json", {"job_id": job_id, "status": "running", "heartbeat": time.time(), "run_path": None})
        try:
            _launch(path, False, api_key)
        except Exception:
            dump(path / "job.json", {"job_id": job_id, "status": "failed", "heartbeat": time.time(), "run_path": None})
            raise
        return job_id
    finally:
        lock.close()


def _ensure_idle():
    for path in workspace().glob("*/job.json"):
        if status(path.parent.name)["status"] == "running":
            raise ValueError("当前已有分析正在运行，请稍后再试；首版同时执行一个任务")


def resume(job_id, api_key):
    lock = acquire(workspace())
    try:
        _ensure_idle()
        value = status(job_id)
        path = job_dir(job_id)
        if value["status"] not in {"partial", "stopped", "interrupted"} or not value.get("run_path"):
            raise ValueError("该任务没有可恢复的检查点")
        (path / "stop.flag").unlink(missing_ok=True)
        dump(path / "job.json", {**value, "status": "running", "heartbeat": time.time()})
        try:
            _launch(path, True, api_key)
        except Exception:
            dump(path / "job.json", {**value, "status": "failed", "heartbeat": time.time()})
            raise
    finally:
        lock.close()


def stop(job_id):
    (job_dir(job_id) / "stop.flag").touch()


def run_path(value):
    relative = value.get("run_path")
    if not relative:
        return None
    root = job_dir(value["job_id"])
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("任务产物路径无效")
    return path


def trace_events(path, limit=60):
    file = Path(path) / "trace.jsonl"
    if not file.exists():
        return []
    result = []
    for line in file.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # the final event may still be writing
    return result


def artifact(path, relative):
    root = Path(path).resolve()
    child = (root / relative).resolve()
    if not child.is_relative_to(root) or not child.is_file():
        raise ValueError("无效的证据路径")
    return child


def download_zip(path):
    """Only documented artifacts, never keys, checkpoints or uploaded raw data."""
    output = io.BytesIO()
    allowed = {".json", ".csv", ".png", ".svg", ".sql", ".md", ".jsonl"}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(Path(path).rglob("*")):
            if file.is_file() and file.suffix in allowed and file.name != "state.json" and "prepared" not in file.parts:
                archive.write(artifact(path, str(file.relative_to(path))), file.relative_to(path).as_posix())
    return output.getvalue()
