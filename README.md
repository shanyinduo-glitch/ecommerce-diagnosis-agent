# 电商销售变化诊断 Agent

使用 **LangChain + Qwen + 自定义 Plan–Execute–Replan 控制器**。输入课程的三张 CSV，模型规划任务、调用数据工具、自选 SQL 下钻、读取错误并修正，最后生成带表格、图表及字段级证据引用的中文报告。

业务计算使用确定性 Python 函数，模型生成的代码使用只读内存 SQLite 执行。没有使用 LangGraph，也没有开放任意 Python、Shell 或宿主文件读写工具。

## 当前能力

- 结构检查、重复去重、冲突全量隔离、未知商品保留、质量影响桥接。
- 总体及渠道/品类净销售额、差额、贡献，订单量、客单价、退款率及渠道访客变化。
- 两个分组视角分别与总体金额守恒。
- Qwen 原生工具调用、任务依赖检查、计划版本记录、自主 SQL 分析与错误修复。
- Markdown、CSV、JSON、PNG 报告；模型调用、实际 Token、耗时与工具轨迹。
- 三案例固定字段评分，以及单独的 SQL 故障恢复实验。
- 退款账面桥接与订单量—净客单价对称分解，两个视角独立守恒，不能重复相加。
- 比较清洗前后最大下降分组、并列情况与排名变化；原始行汇总不解释为真实交易额。
- 缺失退款、商品、渠道或流量时保留不受影响的指标，明确不可用原因并标记 partial。
- 结构化结论检查：指标比较、最大下降来源、退款账面桥接。文字和数字从实际指标生成，不接受任意因果解释。
- 工具边界检查点续跑，校验输入、源码与证据哈希，累计调用预算与用量。
- 每次工具反馈提供权威任务状态、可执行编号和剩余预算；成功 SQL 完成编号，失败 SQL 用原编号修复。

## 最近验证

2026-11-13 切换 `qwen3.8-flash` 并完善任务反馈后，三个案例各三次：9/9 completed，594/594 固定数值字段通过，渠道与品类识别、108 项结构化结论检查均通过。正常实验记录 578,879 Token；两次工具错误均恢复。受控四次请求后续跑、SQL 注入错误恢复也分别通过。本地回归 74 项通过。

完整记录见 [Flash 补验记录](docs/qwen3.8-flash补验记录.md)，正常实验位于 `outputs/evaluations/20261113_160313_02440f/`。首次未适配反馈的 Flash 失败、中断和未执行项保留并单列，不能隐去；不同源码版本不是严格的模型对比实验。三次重复只构成初步稳定性证据。

供组员使用的新素材包：`outputs/teammate_figures/20261113_flash.zip`，含 21 张 PNG 与 21 张 SVG、图注及计算证据。

## 网页体验版

Streamlit 入口为 `streamlit_app.py`，支持历史案例、过程与证据浏览、图表下载，以及组内访问码保护的实时后台分析。默认只浏览历史证据，不调用模型。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

免费托管选择 `codex/streamlit-demo` 分支、`streamlit_app.py`、Python 3.12。详细设置见 [Streamlit 免费部署](docs/Streamlit免费部署.md)。真实 `secrets.toml` 已加入忽略规则；密钥只能在部署者的 Secrets 或进程环境中配置。托管休眠、重启后临时结果可能丢失，需及时下载。

## 命令行安装与配置

需要 Python 3.12。Windows PowerShell 在项目根目录运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe scripts\prepare_data.py
```

本机已经建立 `.venv`，可以直接运行。API 密钥读取进程环境变量 `DASHSCOPE_API_KEY`，不自动加载 `.env`；`.env.example` 仅说明配置项。若在系统设置中新建了变量，需要重新打开终端。

默认模型 `qwen3.8-flash`，地址 `https://dashscope.aliyuncs.com/compatible-mode/v1`，配置见 `configs/default.toml`。可用 `ECOMMERCE_MODEL` 和 `ECOMMERCE_BASE_URL` 覆盖单次 CLI 配置。请求关闭 thinking、使用工具调用，SDK 自动重试关闭，控制器记录实际请求次数。此前 `qwen3.8-27b` 的运行记录保留在原目录，按模型配置分别统计。

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

独立重复实验（每案例三次，共九次）及单列恢复实验：

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_suite --repeats 3
.\.venv\Scripts\python.exe -m evaluation.run_suite --cases case_01 --inject-sql-error
```

每次尝试均保存，partial 与失败也计入分母。仅合并模型配置、数据、源码和模式一致的运行；故障注入、续跑和历史版本分组统计。输出 JSON、CSV 与 Markdown，包括均值、范围、标准差及 Token 计量完整性。三次重复只是初步稳定性证据。

## 断点续跑

```powershell
.\.venv\Scripts\python.exe -m ecommerce_agent.cli --resume outputs\runs\case_01\RUN_ID
.\.venv\Scripts\python.exe -m ecommerce_agent.cli --resume outputs\runs\case_01\RUN_ID --extra-calls 10 --extra-seconds 300
```

默认沿用原配置、历史消息、已完成任务和累计用量；新增预算必须显式指定。源码、输入或已登记证据变化时拒绝续跑。进程锁避免并发恢复同一运行。新检查点从最后保存的工具边界继续，旧首版仅供审计的 state.json 不支持恢复。请求中断且用量未知时会如实标记不完整，不伪造 Token。

## 提供给组员的图表

每个新完整运行生成七张图的 300 dpi PNG 与可缩放 SVG，涵盖趋势、渠道、品类、两个数学分解及两个清洗影响视角；figure_index.json 附图注、单位、时期和来源哈希。不可计算的基础图明确显示原因，不能当作零值图使用。

```powershell
.\.venv\Scripts\python.exe scripts\export_figures.py outputs\runs\case_01\RUN_ID --output outputs\teammate_figures\NEW_BUNDLE
```

支持一次传入多个运行目录。导出包包括图像、图注与对应证据，不修改原运行，不重新调用 API。

需要用当前绘图样式整理历史结果时，先用 `scripts/render_saved.py PATHS... --output NEW_DIRECTORY` 创建独立展示副本，再导出。副本保留原始指标、结论和用量，并记录新渲染器哈希，不算新的 Agent 运行。

## API 配额与恢复验证

若返回 `AllocationQuota.FreeTierOnly`，表示免费额度耗尽且账户仅允许免费调用；程序停止并保存 partial，不会自动启用付费。账户方处理额度后，可在源码未变化时按上述命令续跑。

```powershell
.\.venv\Scripts\python.exe scripts\verify_recovery.py --mode resume
.\.venv\Scripts\python.exe scripts\verify_recovery.py --mode sql-error
```

以上为真实 API 实验：前者在四次请求后受控暂停，再恢复到原累计预算；后者注入 SQL 错误并验证修复。结果与正常运行分别存放在 outputs/recovery，失败及首段用量均保留。

数值正确率按参考答案预先固定字段计算，缺失判错；金额容差 0.01 元，整数精确，客单价半个末位 0.00005，比例半个末位 0.0000005。渠道和品类命中评价计算得到的最大下降分组。模型对流量、退款和客单价机制的解释需要结合 SQL 证据人工核查，不能仅按最大下降分组命中声称机制识别成功。

## 输出

`outputs/runs/<case_id>/<run_id>/` 下有报告、指标、质量报告、清洗和隔离记录、图表、SQL代码、查询结果、计划版本、证据索引、调用轨迹、错误日志、配置及用量。`outputs/evaluations/` 存评测汇总。所有输出留在当前项目；不修改 CausaLens。

`report.md` 的数字来自已计算表格。新运行的结论只允许受支持的结构化事实，由程序验证并生成文字。历史自由文本仍标记为未经语义验证；这不等于验证任意自然语言，更不证明业务因果。

## 第一版边界

- 采用受限 SQL 执行代替原规划中的任意 Python worker，满足课设“Python 或 SQL”要求。SQLite 只允许三个内存表的读取和少量聚合函数，禁止 ATTACH、写操作、PRAGMA、扩展加载，限制返回行数、SQL长度和执行指令数。这不是通用代码沙箱。
- 没有前端、向量库、自动因果估计或跨系统联调。
- 关键订单身份或日期无法确定时停止安全计算；缺失金额不按零补，含无效值的金额字段整体停用。商品主键冲突停用品类拆解，流量不完整停用访客指标，同时保留可核算销售指标。缺失字段时去重/冲突只能依据现有字段，结果标记 partial 并说明限制。
- Token 缺失时在 usage 中标记，不把不完整计量当完整值；调用失败不表示已完成分析。
- 观点是描述性下降来源，不能证明广告、竞品或物流造成了下降。

实现参考：[LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai)、[Qwen Function Calling](https://help.aliyun.com/en/model-studio/qwen-function-calling)。
