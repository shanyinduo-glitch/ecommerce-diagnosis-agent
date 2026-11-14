"""Create a derived presentation copy from saved evidence, preserving original runs."""
import argparse
import json
import shutil
from pathlib import Path
from ecommerce_agent.io import dump, sha
from ecommerce_agent import reporting


def render_runs(paths, destination):
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError("请指定新的派生输出目录，避免覆盖已有结果")
    outputs = []
    for source in paths:
        source = source.resolve()
        if destination.is_relative_to(source):
            raise ValueError("派生目录不能位于原运行目录内")
        def read(name, default=None):
            path = source / name
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
        manifest = read("manifest.json")
        # The source paths are user/CLI inputs, not model-controlled file tools.
        target = destination / source.parent.name / source.name
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(".run.lock"))
        reporting.charts(read("metrics.json"), target, manifest["config"])
        evidence = read("evidence_index.json", {})
        dump(target / "original_evidence_index.json", evidence)
        for entry in evidence.values():
            artifact = (target / entry["path"]).resolve()
            if not artifact.is_relative_to(target) or not artifact.is_file():
                raise ValueError("源证据路径异常")
            entry["sha256"] = sha(artifact)
        dump(target / "evidence_index.json", evidence)
        reporting.report(target, read("metrics.json"), read("quality_report.json"), read("findings.json", []),
                         manifest["status"], read("errors.json", []), manifest["config"], read("usage.json", {}))
        dump(target / "report_render.json", {
            "source_run": str(source), "source_manifest_sha256": sha(source / "manifest.json"),
            "original_report_sha256": sha(source / "report.md"),
            "reporter_sha256": sha(Path(reporting.__file__)), "no_new_model_calls": True,
            "note": "派生展示副本：指标、模型结论、原始用量和分析源码版本不变；仅使用当前绘图与报告程序重新渲染。原始运行未修改。检查点仅供原始运行审计，不在派生副本续跑。"})
        outputs.append(target)
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for path in render_runs(args.paths, args.output):
        print(path)
