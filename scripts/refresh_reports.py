"""Re-render presentation only from saved evidence; never alter original model findings."""
import argparse
import json
from pathlib import Path
from ecommerce_agent.reporting import report
from ecommerce_agent.io import dump, sha

parser=argparse.ArgumentParser()
parser.add_argument("paths",nargs="+",type=Path)
args=parser.parse_args()
for path in args.paths:
    def read(name,default=None):
        p=path/name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    manifest=read("manifest.json")
    report(path,read("metrics.json"),read("quality_report.json"),read("findings.json",[]),manifest["status"],read("errors.json",[]),manifest["config"],read("usage.json"))
    import ecommerce_agent.reporting as module
    dump(path/"report_render.json",{"reporter_sha256":sha(Path(module.__file__)),"note":"Presentation refreshed from original saved evidence; model findings and usage unchanged."})
    print(path/"report.md")
