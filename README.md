# AI 数据分析小面板（MVP）

这是一个从 0 到 1 的小 demo：上传 CSV 数据 → 生成基础分析 → （可选）用 AI 做总结/问答。

## 你将得到

- 上传 CSV
- 数据预览（前几行）
- 基础概览（行数/列数/缺失值/数值列统计）
- 一键“自动洞察”（不需要 API Key 也能用）
- （可选）自然语言提问 & AI 总结（支持 OpenAI / 通义千问）

## 环境要求

- Windows / macOS / Linux
- Python 3.10+

## 运行方式（推荐：零依赖版本）

这个版本不需要 `pip install`，适合你现在这种网络/证书环境可能导致 pip HTTPS 失败的情况。

1) 启动服务

```powershell
py server.py
```

2) 打开浏览器

访问 `http://127.0.0.1:8000`，上传 CSV 即可。

3) （可选）启用大模型问答（推荐：通义千问）

你有两种方式配置 Key（推荐用 `.env`，更省事）：

**方式 A：写 `.env`（推荐）**

```powershell
Copy-Item .env.example .env
notepad .env
py server.py
```

**方式 B：PowerShell 环境变量（临时）**

```powershell
$env:LLM_PROVIDER="dashscope"
$env:LLM_API_KEY="你的百炼API Key"
$env:LLM_MODEL="qwen-turbo"
# 可选：北京（默认）/ 新加坡 / 美国
# 百炼控制台里常见的 Responses 兼容 base_url（推荐按控制台显示的来）
# $env:LLM_BASE_URL="https://dashscope.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1"          # 北京
# $env:LLM_BASE_URL="https://dashscope-intl.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1"    # 新加坡
# $env:LLM_BASE_URL="https://dashscope-us.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1"      # 美国
py server.py
```

如果你想用 OpenAI：

```powershell
$env:LLM_PROVIDER="openai"
$env:LLM_API_KEY="你的OpenAI key"
$env:LLM_MODEL="gpt-4.1-mini"
py server.py
```

## 可选：Streamlit 版本（如果你能正常 pip）

如果你机器能正常安装依赖，可以走更漂亮的 Streamlit UI：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-streamlit.txt
streamlit run app.py
```

## 常见问题

- 没有 OpenAI Key 也能跑吗？
  - 能。没有 Key 时，AI 部分会自动降级为“规则/统计洞察”。

