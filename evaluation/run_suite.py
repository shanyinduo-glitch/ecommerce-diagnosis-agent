"""Run a predeclared experiment, saving every attempt before starting the next."""
from __future__ import annotations

import argparse
import os
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ecommerce_agent.io import ROOT, dump, sha
from evaluation.stability import collect_run, read_json, write_summary


def load_config(path: Path):
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    config["model"] = os.getenv("ECOMMERCE_MODEL", config["model"])
    config["base_url"] = os.getenv("ECOMMERCE_BASE_URL", config["base_url"])
    return config


def run_experiment(cases, repeats, config, refs, dest: Path, *, inject_sql_error=False, run_factory=None):
    if repeats < 1:
        raise ValueError("重复次数必须大于零")
    if run_factory is None:
        from ecommerce_agent.agent import AgentRun
        run_factory = AgentRun
    source_root = ROOT / "src/ecommerce_agent"
    source_hashes = {p.name: sha(p) for p in source_root.glob("*.py")}
    planned = [{"attempt_id": f"{case}_repeat_{repeat:02d}", "case_id": case,
                "repeat": repeat, "state": "pending"}
               for repeat in range(1, repeats + 1) for case in cases]
    protocol = {"started_utc": datetime.now(timezone.utc).isoformat(), "cases": list(cases),
                "repeats": repeats, "config": config, "fault_injection": inject_sql_error,
                "source_code_hashes": source_hashes,
                "evaluator_hashes": {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")},
                "data_hashes": {case: {name: sha(ROOT / "data/raw/public" / case / f"{name}.csv")
                                      for name in ("order_items", "products", "traffic")} for case in cases},
                "attempts": planned, "status": "running"}
    results = []
    dest.mkdir(parents=True, exist_ok=True)
    dump(dest / "protocol.json", protocol)
    for attempt in planned:
        case = attempt["case_id"]
        attempt["state"] = "running"
        dump(dest / "protocol.json", protocol)
        run = None
        fallback = dest / "failed_attempts" / attempt["attempt_id"]
        path = fallback
        failure = None
        phase = "protocol"
        manifest = {"case_id": case, "run_id": attempt["attempt_id"], "config": config,
                    "fault_injection": inject_sql_error, "source_code_hashes": source_hashes,
                    "data_hashes": protocol["data_hashes"][case]}
        try:
            if {p.name: sha(p) for p in source_root.glob("*.py")} != source_hashes:
                raise RuntimeError("SourceChangedDuringSuite")
            if {name: sha(ROOT / "data/raw/public" / case / f"{name}.csv")
                    for name in ("order_items", "products", "traffic")} != protocol["data_hashes"][case]:
                raise RuntimeError("DataChangedDuringSuite")
            phase = "constructor"
            run = run_factory(ROOT / "data/raw/public" / case, dict(config), inject_sql_error=inject_sql_error)
            path = run.output
            phase = "execution"
            outcome = run.run()
            path = Path(outcome["path"])
            saved_manifest = read_json(path / "manifest.json", {})
            if saved_manifest:
                manifest = saved_manifest
        except Exception as exc:
            # Never persist exception bodies that might contain API headers/secrets.
            failure = type(exc).__name__
            if run is not None:
                path = run.output
                try:
                    manifest = read_json(path / "manifest.json", manifest)
                except (OSError, ValueError):
                    pass
            else:
                dump(path / "manifest.json", {**manifest, "status": "failed"})
        try:
            result = collect_run(path, refs[case], manifest=manifest, execution_error=failure,
                                 attempt_id=attempt["attempt_id"])
        except Exception as exc:
            # A corrupt artifact counts as failure instead of aborting the suite.
            dump(fallback / "manifest.json", {**manifest, "status": "failed"})
            failure = f"ArtifactReadError:{type(exc).__name__}"
            phase = "artifacts"
            result = collect_run(fallback, refs[case], manifest=manifest, execution_error=failure,
                                 attempt_id=attempt["attempt_id"])
            result["original_run_path"] = str(path)
        result["repeat"] = attempt["repeat"]
        result["failure_phase"] = phase if failure else None
        results.append(result)
        attempt.update(state=result["usage"]["status"], run_path=result["run_path"], execution_error=result.get("execution_error"))
        dump(dest / "protocol.json", protocol)
        write_summary(dest, results, protocol)
    protocol.update(status="finished", finished_utc=datetime.now(timezone.utc).isoformat())
    dump(dest / "protocol.json", protocol)
    write_summary(dest, results, protocol)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="公开案例评分与独立重复运行；真实运行会调用 Qwen API")
    parser.add_argument("--score-only", nargs="*", type=Path, help="已有运行目录；不再调用 API")
    parser.add_argument("--repeats", type=int, default=1, help="每案例独立运行次数，建议 3")
    parser.add_argument("--cases", nargs="+", default=None)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/default.toml")
    parser.add_argument("--inject-sql-error", action="store_true", help="仅用于单列故障恢复实验")
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/evaluations")
    args = parser.parse_args(argv)
    refs = read_json(ROOT / "evaluation/references/public_reference_answers.json")
    cases = args.cases or list(refs)
    if not cases or len(set(cases)) != len(cases) or any(case not in refs for case in cases):
        parser.error("--cases 必须为不重复的公开案例编号")
    if args.repeats < 1:
        parser.error("--repeats 必须大于零")
    if args.score_only == []:
        parser.error("--score-only 至少需要一个已有运行目录")
    dest = args.output_root / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6])
    if args.score_only is not None:
        results = []
        for path in args.score_only:
            manifest = read_json(path / "manifest.json", {})
            case = manifest.get("case_id")
            if case not in refs:
                parser.error(f"运行目录缺少有效 case_id: {path}")
            if args.cases is None or case in cases:
                results.append(collect_run(path, refs[case], manifest=manifest))
        try:
            write_summary(dest, results, {"mode": "score_only", "no_api_calls": True})
        except ValueError as exc:
            parser.error(str(exc))
    else:
        run_experiment(cases, args.repeats, load_config(args.config), refs, dest,
                       inject_sql_error=args.inject_sql_error)
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
