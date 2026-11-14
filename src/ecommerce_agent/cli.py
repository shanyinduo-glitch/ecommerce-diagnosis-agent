import argparse
import json
import os
import tomllib
from pathlib import Path
from .io import ROOT

def main():
    parser = argparse.ArgumentParser(description="Qwen 电商销售诊断 Agent")
    parser.add_argument("--case", default="case_01", choices=["case_01","case_02","case_03"])
    parser.add_argument("--data-dir", type=Path, help="自定义目录，仅读取三张指定 CSV")
    parser.add_argument("--config", type=Path, help="默认使用 configs/default.toml；续跑默认沿用原配置")
    parser.add_argument("--resume", type=Path, help="从运行目录最近的已保存工具边界续跑，拒绝数据或源码变化")
    parser.add_argument("--extra-calls", type=int, default=0, help="续跑时显式增加累计模型调用预算")
    parser.add_argument("--extra-seconds", type=int, default=0, help="续跑时显式增加累计耗时预算")
    parser.add_argument("--inject-sql-error", action="store_true", help="单列恢复实验：第一次SQL注入不存在字段")
    args = parser.parse_args()
    if args.extra_calls < 0 or args.extra_seconds < 0:
        parser.error("追加预算不能为负")
    if args.resume and (args.data_dir or args.inject_sql_error):
        parser.error("续跑使用原输入与故障标志，不能同时指定 --data-dir 或 --inject-sql-error")
    if not args.resume and (args.extra_calls or args.extra_seconds):
        parser.error("追加预算仅用于 --resume")
    if args.resume and not args.config:
        config = json.loads((args.resume/"state.json").read_text(encoding="utf-8"))["manifest"]["config"]
    else:
        config = tomllib.loads((args.config or ROOT/"configs/default.toml").read_text(encoding="utf-8"))
    config["max_calls"] += args.extra_calls
    config["max_seconds"] += args.extra_seconds
    config["model"] = os.getenv("ECOMMERCE_MODEL",config["model"])
    config["base_url"] = os.getenv("ECOMMERCE_BASE_URL",config["base_url"])
    from .agent import AgentRun
    run = AgentRun.resume(args.resume, config) if args.resume else AgentRun(args.data_dir or ROOT/"data/raw/public"/args.case,config,inject_sql_error=args.inject_sql_error)
    result = run.run()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["status"]=="completed" else 2

if __name__ == "__main__":
    raise SystemExit(main())
