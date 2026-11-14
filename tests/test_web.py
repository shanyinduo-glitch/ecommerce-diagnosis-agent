import io
import json
import time
import tomllib
import zipfile
from pathlib import Path

import pytest
from ecommerce_agent.io import ROOT, dump
from webapp import service


@pytest.fixture
def job_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ECOMMERCE_WEB_ROOT", str(tmp_path / "jobs"))
    return service.workspace()


def test_launch_is_single_task_and_does_not_persist_credentials(job_root, monkeypatch):
    launched = []
    monkeypatch.setattr(service, "_launch", lambda path, resume, key: launched.append(path.name))
    config = tomllib.loads((ROOT / "configs/default.toml").read_text(encoding="utf-8"))
    job = service.start("case_01", {}, "private-placeholder", config)
    assert launched == [job]
    assert "private-placeholder" not in (job_root / job / "request.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="已有分析"):
        service.start("case_02", {}, "private-placeholder", config)
    value = service.status(job)
    dump(job_root / job / "job.json", {**value, "heartbeat": time.time()-100})
    assert service.status(job)["status"] == "interrupted"
    assert service.start("case_02", {}, "private-placeholder", config) != job


def test_upload_rejection_happens_before_launch(job_root, monkeypatch):
    def no_launch(*args):
        pytest.fail("Invalid input must not launch a model worker")
    monkeypatch.setattr(service, "_launch", no_launch)
    with pytest.raises(ValueError, match="三张"):
        service.start("uploaded", {"order_items": b"x"}, "x", {})
    files = {name: b"x" for name in ("order_items", "products", "traffic")}
    files["traffic"] = b"x" * (service.MAX_FILE_BYTES+1)
    with pytest.raises(ValueError, match="8 MB"):
        service.start("uploaded", files, "x", {})
    with pytest.raises(ValueError):
        service.job_dir("../../private")


def test_download_excludes_checkpoints_and_prepared_rows(tmp_path):
    dump(tmp_path / "metrics.json", {"safe": True})
    dump(tmp_path / "state.json", {"messages": "private"})
    dump(tmp_path / "prepared/raw.json", {"private": True})
    files = zipfile.ZipFile(io.BytesIO(service.download_zip(tmp_path))).namelist()
    assert files == ["metrics.json"]
    with pytest.raises(ValueError):
        service.artifact(tmp_path, "../outside")


def test_demo_app_is_read_only_on_first_load(monkeypatch):
    from streamlit.testing.v1 import AppTest
    def no_start(*args, **kwargs):
        pytest.fail("Browsing must not start a live job")
    monkeypatch.setattr(service, "start", no_start)
    app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30).run()
    assert not app.exception
    assert any("1,538,405.57" in item.value for item in app.metric)
    assert any("历史真实运行" in item.value for item in app.caption)


def test_demo_artifacts_are_real_verified_snapshots():
    from ecommerce_agent.claims import validate_findings
    from ecommerce_agent.io import sha
    for case in service.CASES:
        path = ROOT / "demo_runs" / case
        metrics = service.read_json(path/"metrics.json")
        evidence = service.read_json(path/"evidence_index.json")
        findings = service.read_json(path/"findings.json")
        assert validate_findings([item["claim"] for item in findings], metrics, evidence)["passed"]
        for item in evidence.values():
            assert sha(service.artifact(path, item["path"])) == item["sha256"]
        assert not (path/"state.json").exists()
