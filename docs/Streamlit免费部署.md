# Streamlit 免费托管部署

网页入口为根目录 streamlit_app.py。历史案例来自公开模拟数据的真实 Qwen 运行，demo_runs 约 4 MB，不含 API 密钥、输入检查点和原始上传数据。浏览历史案例不会调用模型。

## 托管配置

- GitHub 仓库：shanyinduo-glitch/ecommerce-diagnosis-agent
- 分支：codex/streamlit-demo
- Main file path：streamlit_app.py
- Python：3.12
- Python 依赖：requirements.txt
- Linux 中文字体：packages.txt 的 fonts-noto-cjk

先在 https://share.streamlit.io/ 登录有仓库访问权限的账号，Create app 后选择上述仓库、分支和入口。首次发布只用历史案例也可以，不需要配置模型密钥。

如需启用组员实时运行，在 Advanced settings → Secrets 填入 .streamlit/secrets.example.toml 对应内容：真实 DASHSCOPE_API_KEY、ENABLE_LIVE_ANALYSIS=true 和只分享给组员的 APP_ACCESS_CODE。真实密钥不能提交 GitHub。网页不会要求访问者填写模型密钥。

## 首版行为

- 浏览三个历史真实案例、图表、指标与 SQL 证据，下载完整结果 ZIP。
- 配置组内访问码且明确启用后，才允许创建真实分析任务，消耗后台 Qwen 账户额度。
- 支持课程案例与相同字段结构的三张 CSV 上传，每张 8 MB；上传文件名不作为宿主路径。
- 后台独立进程运行，同一托管实例同时只执行一个任务；忙时明确提示稍后重试，不谎称已经排队。
- 运行后请保存任务恢复码。刷新浏览器后可凭恢复码及组内访问码重新打开。
- 请求停止会在模型请求或工具边界保存检查点，不保证撤回已经发出的请求。
- 续跑按钮显式追加 10 次请求和 300 秒；数据或源码变化会拒绝恢复。
- 结果包不包含 state.json 或 prepared 明细，上传原始文件不包含在下载包内。

## 免费平台边界

Community Cloud 会休眠、重启或重新部署，运行期上传与结果文件不保证持久保存。请及时下载新结果。内置历史证据随仓库部署，可重新打开。部署后必须实际测试校园网和队友宿舍网络可达性，localhost 链接不能分享给异地队友。

## 本地运行

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

本机验证专用 agent API 仅通过命令行参数启用，没有写入部署配置。不要将测试 API 暴露到公网。
