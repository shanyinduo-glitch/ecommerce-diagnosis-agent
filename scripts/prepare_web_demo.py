"""Package selected real public runs, never reference answers or API checkpoints."""
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ecommerce_agent.io import ROOT, dump, sha

RUNS = {"case_01": "20261113_160319_85fb52", "case_02": "20261113_160349_a7a114", "case_03": "20261113_160438_f97576"}
for case, run_id in RUNS.items():
    source = ROOT / "outputs/runs" / case / run_id
    target = ROOT / "demo_runs" / case
    if target.exists():
        raise FileExistsError(f"Already packaged: {target}")
    target.mkdir(parents=True)
    for file in source.rglob("*"):
        if not file.is_file() or file.name in {"state.json", ".run.lock"} or "prepared" in file.parts:
            continue
        destination = target / file.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, destination)
    dump(target / "demo_origin.json", {"case_id": case, "run_id": run_id, "source_manifest_sha256": sha(source/"manifest.json"),
         "note": "课程公开模拟数据的真实运行快照；未新增模型调用，未包含检查点或原始上传数据。"})
print(ROOT / "demo_runs")
