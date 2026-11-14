"""Minimal access diagnosis: no business data, no headers or credentials printed."""
import os
import tomllib
from ecommerce_agent.io import ROOT
from langchain_openai import ChatOpenAI

config = tomllib.loads((ROOT / "configs/default.toml").read_text(encoding="utf-8"))
key = os.environ.get("DASHSCOPE_API_KEY", "")
try:
    client = ChatOpenAI(model=os.getenv("ECOMMERCE_MODEL", config["model"]),
        base_url=os.getenv("ECOMMERCE_BASE_URL", config["base_url"]), api_key=key,
        max_retries=0, timeout=30, max_tokens=8, extra_body={"enable_thinking": False})
    response = client.invoke("请回复 OK")
    print({"status": "ok", "usage": response.usage_metadata})
except Exception as exc:
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    def safe(value):
        value = str(value)
        return (value.replace(key, "[REDACTED]") if key else value)[:600]
    print({"exception": type(exc).__name__, "status": getattr(exc, "status_code", None),
           "code": safe(error.get("code")), "type": safe(error.get("type")),
           "message": safe(error.get("message"))})
