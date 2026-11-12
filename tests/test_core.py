import json
import sqlite3
import tomllib
import pandas as pd
import pytest
from ecommerce_agent import analysis
from ecommerce_agent.io import ROOT
from ecommerce_agent.sql import ReadonlySQL
from ecommerce_agent.agent import AgentRun
from evaluation.scorer import score

@pytest.fixture
def config():
    return tomllib.loads((ROOT/"configs/default.toml").read_text(encoding="utf-8"))

@pytest.fixture
def tables():
    return analysis.read_tables(ROOT/"data/raw/public/case_01")

@pytest.mark.parametrize("case",["case_01","case_02","case_03"])
def test_public_metrics(case,tmp_path,config):
    frames=analysis.read_tables(ROOT/"data/raw/public"/case)
    items,traffic,q=analysis.clean(frames,tmp_path/"prepared",config)
    metrics=analysis.compute(items,traffic,q,tmp_path/"tables")
    refs=json.loads((ROOT/"evaluation/references/public_reference_answers.json").read_text(encoding="utf-8"))
    result=score(metrics,refs[case])
    assert result["numeric_accuracy"]==1, [x for x in result["details"] if not x["passed"]]
    assert result["channel_hit"] and result["category_hit"]
    assert analysis.verify(metrics)["passed"]
    assert len(items)==len(frames["order_items"])-20
    assert q["unmatched_product_rows"]==8

def test_missing_field_rejected(tables,tmp_path,config):
    tables["order_items"]=tables["order_items"].drop(columns="refund_amount")
    assert analysis.inspect(tables)["order_items"]["missing_columns"]==["refund_amount"]
    with pytest.raises(ValueError,match="missing_columns"):
        analysis.clean(tables,tmp_path,config)

def test_duplicate_traffic_rejected(tables,tmp_path,config):
    tables["traffic"]=pd.concat([tables["traffic"],tables["traffic"].iloc[:1]])
    with pytest.raises(ValueError,match="流量"):
        analysis.clean(tables,tmp_path,config)

def test_hand_calculated_conflict_and_unknown(tmp_path,config):
    base={"order_id":"O1","item_id":"I1","date":"2026-09-01","product_id":"P1","channel":"A","quantity":"2","gross_amount":"100.00","discount_amount":"10.00","refund_amount":"5.00"}
    unknown={**base,"order_id":"O2","item_id":"I2","product_id":"absent","date":"2026-09-15"}
    c1={**base,"item_id":"I3","order_id":"O3"};c2={**c1,"refund_amount":"9.00"}
    frames={"order_items":pd.DataFrame([base,base,unknown,c1,c2]),"products":pd.DataFrame([{"product_id":"P1","product_name":"x","category":"C","list_price":"50.00"}]),"traffic":pd.DataFrame([{"date":"2026-09-01","channel":"A","visitors":"10"},{"date":"2026-09-15","channel":"A","visitors":"10"}])}
    frames["traffic"]=pd.DataFrame([{"date":d,"channel":"A","visitors":"10"} for d in pd.date_range("2026-09-01","2026-09-28").strftime("%Y-%m-%d")])
    items,traffic,q=analysis.clean(frames,tmp_path/"prepared",config)
    m=analysis.compute(items,traffic,q,tmp_path/"tables")
    assert len(items)==2 and q["excluded_raw_net_sales"]==166
    assert m["overall"]["control"]["net_sales"]==85 # no multiplication by quantity
    assert m["overall"]["difference"]==0
    assert m["top_decline_source"]["category"] is None
    assert all(x["contribution"] is None for x in m["by_category"].values())
    assert analysis.ratio(1,0) is None
    assert m["by_category"]["未知"]["analysis_net_sales"]==85

@pytest.fixture
def sql(tables,tmp_path,config):
    items,traffic,_=analysis.clean(tables,tmp_path,config)
    db=ReadonlySQL(items,traffic)
    yield db
    db.close()

@pytest.mark.parametrize("query",["DELETE FROM items","ATTACH DATABASE 'x.db' AS x","PRAGMA database_list","SELECT load_extension('x')","SELECT * FROM sqlite_master","SELECT randomblob(1000000000)"])
def test_sql_denies_external_access(sql,query):
    with pytest.raises(sqlite3.DatabaseError): sql.run(query)

def test_sql_error_then_corrected(sql):
    with pytest.raises(sqlite3.OperationalError):sql.run("SELECT missing_column FROM items")
    assert sql.run("SELECT COUNT(*) AS n FROM items")["rows"]==[{"n":10236}]

def test_oversized_result(sql):
    with pytest.raises(ValueError,match="200"): sql.run("SELECT * FROM items")

def test_plan_cycle_and_finish_gate(tmp_path,config):
    run=AgentRun(ROOT/"data/raw/public/case_01",config,output_root=tmp_path)
    tools={t.name:t for t in run.build_tools()}
    with pytest.raises(ValueError,match="依赖"):
        tools["update_plan"].invoke({"tasks":[{"id":"a","objective":"x","depends_on":["b"]},{"id":"b","objective":"y","depends_on":["a"]}],"reason":"test"})
    with pytest.raises(ValueError,match="必做"):
        tools["finish"].invoke({"task_id":"finish","findings":[{"statement":"done","evidence_ids":["invented"]}]})

def test_missing_score_does_not_shrink_denominator():
    ref=json.loads((ROOT/"evaluation/references/public_reference_answers.json").read_text(encoding="utf-8"))["case_01"]
    s=score({},ref)
    assert s["numeric_total"]>60 and s["numeric_correct"]==0

def test_missing_traffic_not_treated_as_zero(tables,tmp_path,config):
    tables["traffic"]=tables["traffic"].iloc[1:].copy()
    with pytest.raises(ValueError,match="缺少"):
        analysis.clean(tables,tmp_path,config)

def test_duplicate_product_rejected(tables,tmp_path,config):
    tables["products"]=pd.concat([tables["products"],tables["products"].iloc[:1]])
    with pytest.raises(ValueError,match="商品主键"):
        analysis.clean(tables,tmp_path,config)

def test_budget_stop_and_missing_token_usage(tmp_path,config,monkeypatch):
    import ecommerce_agent.agent as module
    from langchain_core.messages import AIMessage
    class NoActionModel:
        def bind_tools(self,*args,**kwargs): return self
        def invoke(self,messages): return AIMessage(content="Need more time")
    monkeypatch.setenv("DASHSCOPE_API_KEY","test-placeholder")
    monkeypatch.setattr(module,"ChatOpenAI",lambda **kwargs:NoActionModel())
    config["max_calls"]=1
    run=AgentRun(ROOT/"data/raw/public/case_01",config,output_root=tmp_path)
    result=run.run()
    assert result["status"]=="partial" and result["model_calls"]==1
    assert not result["token_totals_complete"]
    assert (run.output/"report.md").exists()

def test_sql_instruction_budget(sql):
    with pytest.raises(sqlite3.OperationalError,match="interrupted"):
        sql.run("SELECT SUM(a.net_cents+b.net_cents) AS n FROM items a CROSS JOIN items b")
