# README - 股票行情查询助手

基于 qwen-agent 的自然语言股票行情查询助手：用户用中文提问，助手自动生成 SQL 查询本地行情库，返回完整数据表格与可视化图表。

## 功能特性

- 自然语言转 SQL，覆盖行情查询、区间统计、多股票对比等场景
- 支持相对时间提问（如"最近一个月"），由 time MCP 服务提供当前时间
- 查询结果完整返回给大模型，不做行数截断
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

## 环境准备

1. 安装依赖（建议使用虚拟环境）：

```powershell
pip install -r requirements.txt
```

2. 配置环境变量：

```powershell
$env:DASHSCOPE_API_KEY="你的 DashScope API Key"
```

仅重新抓取数据时需要：

```powershell
$env:TUSHARE_TOKEN="你的 tushare token"
```

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
2. 抓取数据报缺少 token：确认已设置 `TUSHARE_TOKEN`。
3. 图表中文显示为方块：系统缺少中文字体，代码已按 SimHei、Microsoft YaHei、SimSun 顺序回退，可安装其中之一。
4. 数据为空或表不存在：先执行 `python fetch_stock_history.py` 生成数据库。
5. 返回内容 token 消耗较大：工具会把完整查询结果交给大模型，建议在提问时用 SQL 聚合（如按周、按月汇总）缩小结果集。
