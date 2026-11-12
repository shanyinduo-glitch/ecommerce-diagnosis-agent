# 电商销售变化诊断 Agent

第一版使用 **LangChain + Qwen + 自定义 Plan–Execute–Replan 控制器**。输入课程的三张 CSV，模型规划任务、调用数据工具、自选 SQL 下钻、读取错误并修正，最后生成带表格、三张图及证据引用的中文报告。

业务计算使用确定性 Python 函数，模型生成的代码使用只读内存 SQLite 执行。没有使用 LangGraph，也没有开放任意 Python、Shell 或宿主文件读写工具。

## 当前能力

- 结构检查、重复去重、冲突全量隔离、未知商品保留、质量影响桥接。
- 总体及渠道/品类净销售额、差额、贡献，订单量、客单价、退款率及渠道访客变化。
- 两个分组视角分别与总体金额守恒。
- Qwen 原生工具调用、任务依赖检查、计划版本记录、自主 SQL 分析与错误修复。
- Markdown、CSV、JSON、PNG 报告；模型调用、实际 Token、耗时与工具轨迹。
- 三案例固定字段评分，以及单独的 SQL 故障恢复实验。

## 安装与配置

需要 Python 3.12。Windows PowerShell 在项目根目录运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe scripts\prepare_data.py
```

本机已经建立 `.venv`，可以直接运行。API 密钥读取进程环境变量 `DASHSCOPE_API_KEY`，不自动加载 `.env`；`.env.example` 仅说明配置项。若在系统设置中新建了变量，需要重新打开终端。

默认模型 `qwen3.8-27b`，地址 `https://dashscope.aliyuncs.com/compatible-mode/v1`，配置见 `configs/default.toml`。可用 `ECOMMERCE_MODEL` 和 `ECOMMERCE_BASE_URL` 覆盖单次 CLI 配置。请求关闭 thinking、使用工具调用，SDK 自动重试关闭，控制器记录实际请求次数。

## 运行

```powershell
.\.venv\Scripts\python.exe -m ecommerce_agent.cli --case case_01
.\.venv\Scripts\python.exe -m ecommerce_agent.cli --case case_02
.\.venv\Scripts\python.exe -m ecommerce_agent.cli --case case_03
```

也可运行 `run_agent.cmd case_01`。CLI 退出码 0 表示完成，2 表示部分完成。输出目录打印在终端。

自定义目录必须包含三张同结构 CSV，时期可通过 `--config` 指定。模型会接收样例和聚合结果，因此仅对已获授权发送到模型服务的数据使用此入口。

```powershell
.\.venv\Scripts\python.exe -m ecommerce_agent.cli --data-dir YOUR_CASE_FOLDER --config YOUR_CONFIG.toml
```

## 评测

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m evaluation.run_suite
.\.venv\Scripts\python.exe -m ecommerce_agent.cli --case case_01 --inject-sql-error
```

`run_suite` 会真实调用模型运行三个案例，有 API 用量；`--score-only` 后接已有运行目录可以只评分。正常运行和注入故障运行分开标记。

数值正确率按参考答案预先固定字段计算，缺失判错；金额容差 0.01 元，整数精确，客单价半个末位 0.00005，比例半个末位 0.0000005。渠道和品类命中评价计算得到的最大下降分组。模型对流量、退款和客单价机制的解释需要结合 SQL 证据人工核查，不能仅按最大下降分组命中声称机制识别成功。

## 输出

`outputs/runs/<case_id>/<run_id>/` 下有报告、指标、质量报告、清洗和隔离记录、图表、SQL代码、查询结果、计划版本、证据索引、调用轨迹、错误日志、配置及用量。`outputs/evaluations/` 存评测汇总。所有输出留在当前项目；不修改 CausaLens。

`report.md` 的数字来自已计算表格。模型解释明确标注并引用证据；引用存在不等于自然语言解释已经经过语义证明。

## 第一版边界

- 采用受限 SQL 执行代替原规划中的任意 Python worker，满足课设“Python 或 SQL”要求。SQLite 只允许三个内存表的读取和少量聚合函数，禁止 ATTACH、写操作、PRAGMA、扩展加载，限制返回行数、SQL长度和执行指令数。这不是通用代码沙箱。
- 没有前端、向量库、自动因果估计或跨系统联调。
- 状态快照用于审计，尚未实现中断后的自动续跑。
- 关键字段缺失、商品主键冲突或流量不完整时，返回检查结果和 partial，第一版不会自动推断修补缺失数据，也不保证继续计算所有不受影响的指标。
- Token 缺失时在 usage 中标记，不把不完整计量当完整值；调用失败不表示已完成分析。
- 观点是描述性下降来源，不能证明广告、竞品或物流造成了下降。

实现参考：[LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai)、[Qwen Function Calling](https://help.aliyun.com/en/model-studio/qwen-function-calling)。
