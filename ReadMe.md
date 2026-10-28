# README - 股票行情查询助手

基于 qwen-agent 的自然语言股票行情查询助手：用户用中文提问，助手自动生成 SQL 查询本地行情库，返回统计摘要、结果预览、完整数据下载链接与可视化图表。

## 功能特性

- 自然语言转 SQL，覆盖行情查询、区间统计、多股票对比等场景
- 支持相对时间提问（如"最近一个月"），由 time MCP 服务提供当前时间
- 冰山式上下文治理：完整结果落盘为 CSV，进入大模型的只有统计摘要 + 前 80 行 × 12 列预览 + 下载链接
- 根据结果规模自动选择折线图或堆积柱状图，图表标签自动抽稀、避免重叠
- 支持 Web 图形界面与终端交互两种运行模式

## 目录结构

| 文件 | 说明 |
| --- | --- |
| `assistant_stock_bot.py` | 助手主程序：system prompt、exc_sql 工具、可视化、WebUI/CLI 入口 |
| `fetch_stock_history.py` | 数据抓取脚本：调用 tushare 拉取日线行情，写入 Excel 与 SQLite |
| `schema.sql` | `stock_daily` 建表语句 |
| `stock_history.db` | SQLite 行情数据库（由抓取脚本生成） |
| `stock_history.xlsx` | 行情数据 Excel 备份 |
| `faq.txt` | 注入给大模型的问题示例文件 |
| `requirements.txt` | 依赖清单 |
| `Agent.md` | Agent 角色、工具与输出约定说明 |
| `image_show/` | 运行时自动生成的图表目录 |
| `exports/` | 运行时自动生成的完整数据 CSV 目录 |

## 环境准备

1. 安装依赖（建议使用虚拟环境）：

```powershell
pip install -r requirements.txt
```

依赖中的 `uv` 会提供 `uvx` 命令，用于启动 time MCP 服务；缺失时助手初始化会失败。

2. 配置环境变量：

```powershell
$env:DASHSCOPE_API_KEY="你的 DashScope API Key"
```

仅重新抓取数据时需要：

```powershell
$env:TUSHARE_TOKEN="你的 tushare token"
```

## 模型配置

`init_agent_service()` 中的 `llm_cfg` 当前为：

```python
'llm_cfg': {
    'model': 'qwen3.8-max-0902',
    'model_type': 'oai',
    'model_server': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    'api_key': os.getenv('DASHSCOPE_API_KEY', ''),
    ...
}
```

qwen3.x 系列模型不在 DashScope 经典 text-generation 端点上，必须走 OpenAI 兼容端点。若换成 `qwen-max` 这类经典端点模型，需要删除 `model_type` 与 `model_server` 两行；否则启动正常但提问会报 `Throttling.RateQuota` 或 url error。

## 数据准备

数据库已包含 2020-01-01 至今、4 只股票的日线数据。如需更新数据：

```powershell
python fetch_stock_history.py
```

脚本会依次拉取贵州茅台、五粮液、广发证券、中芯国际的行情，按交易日排序后覆盖写入 `stock_history.db` 的 `stock_daily` 表，同时导出 `stock_history.xlsx`。

## 运行

```powershell
python assistant_stock_bot.py
```

默认启动 Web 图形界面（qwen-agent WebUI）。终端交互模式请将文件末尾的启动调用改为：

```python
app_tui()
```

## 示例问题

- 查询2025年全年贵州茅台的收盘价走势
- 统计2025年4月广发证券的日均成交量
- 对比2025年中芯国际和贵州茅台的涨跌幅

## 查询结果处理

`exc_sql` 工具按结果规模分三种处理方式：

| 结果规模 | 返回内容 |
| --- | --- |
| 0 或 1 行 | 仅 markdown 表格，不做统计、不画图、不落盘 |
| 超过 1 行 | 统计摘要 + 预览表格 + 图表；结果超出预览阈值时追加 CSV 下载链接 |

预览阈值由文件顶部常量控制：

- `MAX_PREVIEW_ROWS = 80`：进入上下文的预览行数
- `MAX_PREVIEW_COLS = 12`：进入上下文的预览列数
- `MAX_SESSION_CACHE = 100`：会话 DataFrame 缓存的 LRU 上限

完整数据始终写入 `exports/export_<时间戳>.csv`，图表基于完整结果绘制，保存到 `image_show/`。

## 数据库说明

表 `stock_daily` 主要字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ts_code` | TEXT | 股票代码，如 600519.SH |
| `name` | TEXT | 股票名称 |
| `trade_date` | TEXT | 交易日期，格式 YYYY-MM-DD |
| `open` / `high` / `low` / `close` | REAL | 开盘价、最高价、最低价、收盘价 |
| `pre_close` / `change` / `pct_chg` | REAL | 昨收价、涨跌额、涨跌幅（%） |
| `vol` / `amount` | REAL | 成交量（手）、成交额（千元） |

## 常见问题

1. 启动报 API Key 相关错误：确认已设置 `DASHSCOPE_API_KEY`，且当前终端环境变量生效。
2. 提问报 `Throttling.RateQuota` 或 `InvalidParameter url error`：模型名与端点不匹配，按"模型配置"一节确认 `model_type` / `model_server`，或改用当前账号可用的模型名。
3. 启动报 MCP 相关错误：确认 `uvx` 可用（`uvx --version`），否则重新执行 `pip install -r requirements.txt`。
4. 抓取数据报缺少 token：确认已设置 `TUSHARE_TOKEN`。
5. 图表中文显示为方块：系统缺少中文字体，代码已按 SimHei、Microsoft YaHei、SimSun 顺序回退，可安装其中之一。
6. 数据为空或表不存在：先执行 `python fetch_stock_history.py` 生成数据库。
7. 需要完整数据：查询结果超过预览阈值时，点开回复中的【完整数据下载】链接，或直接查看 `exports/` 目录下最新的 CSV。
