"""Trusted background worker; credentials are inherited, never written to disk."""
import argparse
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ecommerce_agent.agent import AgentRun
from ecommerce_agent.io import ROOT, dump
from webapp.service import job_dir, read_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    path = job_dir(args.job_id)
    value = read_json(path / "job.json")
    request = read_json(path / "request.json")
    done = threading.Event()
    def heartbeat():
        while not done.wait(2):
            value["heartbeat"] = time.time()
            dump(path / "job.json", value)
    thread = threading.Thread(target=heartbeat, daemon=True)
    try:
        if args.resume:
            run_folder = (path / value["run_path"]).resolve()
            if not run_folder.is_relative_to(path):
                raise ValueError("InvalidRunPath")
            manifest = read_json(run_folder / "manifest.json")
            config = {**manifest["config"], "max_calls": manifest["config"]["max_calls"] + 10,
                      "max_seconds": manifest["config"]["max_seconds"] + 300}
            run = AgentRun.resume(run_folder, config)
        else:
            folder = path / "input" if request["case"] == "uploaded" else ROOT / "data/raw/public" / request["case"]
            run = AgentRun(folder, request["config"], output_root=path / "runs")
        run.cancel_file = path / "stop.flag"
        value["run_path"] = run.output.relative_to(path).as_posix()
        dump(path / "job.json", value)
        thread.start()
        result = run.run()
        value["status"] = "stopped" if run.cancel_file.exists() else result["status"]
    except Exception as exc:
        value["status"] = "failed"
        value["error"] = type(exc).__name__  # no remote bodies or credentials
    finally:
        done.set()
        if thread.is_alive():
            thread.join()
        value["heartbeat"] = time.time()
        dump(path / "job.json", value)


if __name__ == "__main__":
    main()
