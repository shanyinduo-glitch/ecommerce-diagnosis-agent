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
    parser.add_argument("--config", type=Path, default=ROOT/"configs/default.toml")
    parser.add_argument("--inject-sql-error", action="store_true", help="单列恢复实验：第一次SQL注入不存在字段")
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    config["model"] = os.getenv("ECOMMERCE_MODEL",config["model"])
    config["base_url"] = os.getenv("ECOMMERCE_BASE_URL",config["base_url"])
    from .agent import AgentRun
    result = AgentRun(args.data_dir or ROOT/"data/raw/public"/args.case,config,inject_sql_error=args.inject_sql_error).run()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["status"]=="completed" else 2

if __name__ == "__main__":
    raise SystemExit(main())
