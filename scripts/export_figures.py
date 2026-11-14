"""Copy saved figures and their evidence into a self-contained teammate bundle.

No model calls and no modification or re-rendering of the original run.
"""
from __future__ import annotations
import argparse
import json
import re
import shutil
from pathlib import Path
from ecommerce_agent.io import dump, sha


def safe_child(root, relative):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("产物路径必须在运行目录内")
    return target


def export_runs(paths, dest):
    dest = Path(dest).resolve()
    if dest.exists():
        raise FileExistsError(f"为避免覆盖，请使用新的输出目录：{dest}")
    plans = []
    for source in paths:
        source = Path(source).resolve()
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        index = json.loads((source / "figure_index.json").read_text(encoding="utf-8"))
        case, run = manifest["case_id"], manifest["run_id"]
        if not all(re.fullmatch(r"[\w-]+", value) for value in (case, run)):
            raise ValueError("案例与运行标识不能作为安全的子目录名")
        files = {"metrics.json", "figure_index.json"}
        if (source / "report_render.json").exists():
            files.add("report_render.json")
        for figure in index["figures"]:
            png = safe_child(source, figure["png"])
            if sha(png) != figure["png_sha256"]:
                raise ValueError(f"图像哈希不符：{png}")
            files.update([figure["png"], figure["svg"]])
            for relative, digest in figure.get("source_hashes", {}).items():
                if sha(safe_child(source, relative)) != digest:
                    raise ValueError(f"证据哈希不符：{relative}")
                files.add(relative)
        for relative in files:
            safe_child(source, relative).stat()
        plans.append((source, case, run, manifest, index, files))
    if len({(case, run) for _, case, run, _, _, _ in plans}) != len(plans):
        raise ValueError("重复运行目录")
    lines = ["# 供课程报告使用的图表与证据", "", "图片来自已保存的分析运行；本次导出未重新调用模型或修改原始运行。PNG 为 300 dpi，SVG 可缩放编辑。请保留图注、时间、单位及数据来源。", "",
             "标为不可计算的提示图不能用作零值结果。渠道与品类贡献不能相加；数学分解不证明业务因果。", ""]
    for source, case, run, manifest, index, files in plans:
        target = dest / case / run
        for relative in sorted(files):
            copied = safe_child(target, relative)
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(safe_child(source, relative), copied)
        dump(target / "source_manifest.json", {"source_run": str(source), "case_id": case, "run_id": run,
             "status": manifest.get("status"), "fault_injection": manifest.get("fault_injection", False),
             "data_hashes": manifest.get("data_hashes"), "source_code_hashes": manifest.get("source_code_hashes"),
             "model": manifest.get("config", {}).get("model"), "files": {f: sha(target / f) for f in sorted(files)}})
        lines += [f"## {case} / {run}", "", index["comparison"], ""]
        for figure in index["figures"]:
            link = f"{case}/{run}/{figure['png']}"
            lines += [f"- [{figure['id']}]({link})：{figure['caption']}" + (" **不可计算提示图**" if not figure.get("available", True) else "")]
        lines.append("")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="包含 figure_index.json 的新运行目录")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(export_runs(args.paths, args.output))


if __name__ == "__main__":
    main()
