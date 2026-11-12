import argparse
import json
import tomllib
from datetime import datetime
from pathlib import Path
from ecommerce_agent.io import ROOT, dump
from ecommerce_agent.agent import AgentRun
from evaluation.scorer import score_run

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--score-only", nargs="*", type=Path, help="已有运行目录；不再调用 API")
    args=parser.parse_args()
    refs=json.loads((ROOT/"evaluation/references/public_reference_answers.json").read_text(encoding="utf-8"))
    config=tomllib.loads((ROOT/"configs/default.toml").read_text(encoding="utf-8"))
    paths=args.score_only
    if paths is None:
        paths=[Path(AgentRun(ROOT/"data/raw/public"/case,config).run()["path"]) for case in refs]
    results=[]
    for p in paths:
        manifest=json.loads((p/"manifest.json").read_text(encoding="utf-8"))
        results.append({"case_id":manifest["case_id"],"fault_injection":manifest["fault_injection"],**score_run(p,refs[manifest["case_id"]])})
    dest=ROOT/"outputs/evaluations"/datetime.now().strftime("%Y%m%d_%H%M%S")
    dump(dest/"results.json",results)
    lines=["# 公开案例评测", "", "数值评分使用预先固定的参考字段，缺失字段计错。正常运行与故障注入运行分开标记。主要来源命中为计算得到的最大下降分组；模型对机制的解释另行人工核查。", "", "| 案例 | 故障注入 | 状态 | 数值正确 | 渠道命中 | 品类命中 | 模型调用 | 工具调用 | Token | 秒 |", "|---|---|---|---|---|---|---:|---:|---:|---:|"]
    for r in results:
        u=r["usage"]
        lines.append(f"| {r['case_id']} | {r['fault_injection']} | {u['status']} | {r['numeric_correct']}/{r['numeric_total']} | {r['channel_hit']} | {r['category_hit']} | {u['model_calls']} | {u['tool_calls']} | {u['total_tokens']} | {u['seconds']} |")
    (dest/"summary.md").write_text("\n".join(lines),encoding="utf-8")
    print(dest)

if __name__=="__main__": main()
