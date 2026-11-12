"""Copy supplied public inputs without overwriting changed files; keep answers evaluator-only."""
from pathlib import Path
import hashlib
import shutil
import json

root = Path(__file__).resolve().parents[1]
source = root/"课题二_资料与项目规划"
manifest = []
for case in ("case_01","case_02","case_03"):
    for name in ("order_items.csv","products.csv","traffic.csv"):
        src = source/"02_公开数据"/case/name
        dst = root/"data/raw/public"/case/name
        dst.parent.mkdir(parents=True,exist_ok=True)
        if dst.exists() and dst.read_bytes()!=src.read_bytes(): raise RuntimeError(f"Refuse overwrite: {dst}")
        shutil.copy2(src,dst)
        manifest.append({"path":str(dst.relative_to(root)),"source":str(src.relative_to(root)),"sha256":hashlib.sha256(dst.read_bytes()).hexdigest()})
ref = root/"evaluation/references/public_reference_answers.json"
ref.parent.mkdir(parents=True,exist_ok=True)
shutil.copy2(source/"03_开发评测参考/public_reference_answers.json",ref)
(root/"data/manifests").mkdir(parents=True,exist_ok=True)
(root/"data/manifests/public.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
print("Prepared 9 CSV inputs; references copied separately.")
