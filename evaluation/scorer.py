import json
from pathlib import Path

SECTIONS = ("overall", "by_channel", "by_category", "traffic_by_channel", "data_quality")

def flatten(obj, prefix=""):
    result = {}
    for k,v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v,dict): result.update(flatten(v,key))
        elif isinstance(v,(int,float)): result[key] = v
    return result

def score(actual, expected):
    wanted = flatten({k:expected[k] for k in SECTIONS})
    got = flatten(actual)
    details = []
    for key,value in wanted.items():
        tol = 0 if isinstance(value,int) else (0.00005 if key.endswith("aov") else 0.0000005 if key.endswith(("rate",)) else 0.010000001)
        match = key in got and abs(got[key]-value)<=tol
        details.append({"field":key,"expected":value,"actual":got.get(key),"passed":match,"tolerance":tol})
    return {"numeric_correct":sum(x["passed"] for x in details),"numeric_total":len(details),
            "numeric_accuracy":sum(x["passed"] for x in details)/len(details),
            "channel_hit":actual.get("top_decline_source",{}).get("channel")==expected["top_decline_source"]["channel"],
            "category_hit":actual.get("top_decline_source",{}).get("category")==expected["top_decline_source"]["category"],"details":details}

def score_run(path:Path, expected):
    metrics_path = path/"metrics.json"
    actual = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    result = score(actual,expected)
    result["run_path"] = str(path)
    result["usage"] = json.loads((path/"usage.json").read_text(encoding="utf-8"))
    return result
