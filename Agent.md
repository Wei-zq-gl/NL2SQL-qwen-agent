# Agent.md - 股票查询助手（assistant_stock_bot.py）

本文档描述该 Agent 的角色设定、工具能力、运行配置和输出约定，供后续维护与二次开发参考。

## 1. 角色定义

- 名称：股票查询助手
- 描述：股票行情查询与分析
- 底层模型：`qwen3.8-max-0902`，通过 DashScope 的 OpenAI 兼容端点调用
- 定位：把用户的自然语言问题转换为 SQL，查询本地 SQLite 行情库，返回统计摘要、结果预览、下载链接与可视化图表

## 2. 核心工作流

```
用户自然语言提问
    -> LLM 理解问题并生成 SQLite SQL
    -> 调用 exc_sql 工具执行 SQL
    -> 完整结果写入 exports/*.csv
    -> 计算数值列统计摘要，截取预览表格
    -> 基于完整结果生成可视化图片（PNG）
    -> 组装 摘要 + 预览 + 下载链接 + 图片 markdown 返回给 LLM
    -> LLM 按 system prompt 规则输出给用户
```

## 3. 工具能力

### 3.1 exc_sql（自定义工具）

- 实现类：`ExcSQLTool`，通过 `@register_tool('exc_sql')` 注册
- 参数：`sql_input`（string，必填），即待执行的 SQL 语句
- 执行方式：`pandas.read_sql(text(sql), engine)`，引擎为 SQLite（`stock_history.db`）
- 返回内容按结果规模区分：
  - 0 或 1 行：仅返回 markdown 表格，不做统计、不画图、不落盘
  - 超过 1 行：返回"查询结果规模 + 数值列统计摘要 + 预览表格 + 图片 markdown"
  - 结果超出预览阈值时，追加【完整数据下载】链接，指向 `exports/export_<时间戳>.csv`
- 预览阈值：`MAX_PREVIEW_ROWS = 80`、`MAX_PREVIEW_COLS = 12`；图表始终基于完整 DataFrame 绘制，不受预览限制
- 会话隔离：以首条用户消息内容 + 消息条数生成 md5 指纹作为 `session_id`，把最近一次查询的 DataFrame 存入 `_last_df_dict`，超过 `MAX_SESSION_CACHE = 100` 时按 LRU 淘汰

### 3.2 时间服务（MCP）

- 通过 `uvx mcp-server-time --local-timezone=Asia/Shanghai` 启动
- 用途：处理"今天""本周""最近一个月"等相对时间问题
- 依赖 `uvx` 命令，缺失时 Assistant 初始化失败

### 3.3 知识文件

- `files=['faq.txt']`：注入常见问题示例，规范复杂问题（如区间涨跌幅对比）的拆解思路
- 注意：该配置会让每次提问额外触发一次 Memory 的 LLM 调用

## 4. 数据源与表结构

- 数据库：`stock_history.db`（SQLite），表 `stock_daily`
- 覆盖股票：贵州茅台(600519.SH)、五粮液(000858.SZ)、广发证券(000776.SZ)、中芯国际(688981.SH)
- 时间范围：2020-01-01 至数据抓取日
- 关键字段：`ts_code`、`name`、`trade_date`（YYYY-MM-DD 文本）、`open`、`high`、`low`、`close`、`pre_close`、`change`、`pct_chg`、`vol`、`amount`
- 数据库由 `fetch_stock_history.py` 配合 `schema.sql` 生成

## 5. 可视化规则

- 横坐标长度超过 `LINE_CHART_THRESHOLD`（20）时使用折线图，否则使用柱状图
- 第一列为分类维度；若除第一列外还存在 object 类型列，则先 `pivot_table` 再按分类绘制堆积柱状图或折线图
- 数值列直接按列绘制堆积柱状图或折线图
- 横坐标标签较多时按步长抽稀显示，避免重叠
- 所有标签经 `_safe_label` 处理，规避 `%`、`{}` 等格式化字符导致的报错
- 图片保存到脚本目录下的 `image_show/`（自动创建），markdown 中以相对路径引用

## 6. System Prompt 约定

- 角色：股票行情查询助手，掌握 `stock_daily` 表字段
- 数据库为 SQLite，表名统一 `stock_daily`；`trade_date` 按字符串比较与排序
- 提到股票名称时用 `name` 过滤，提到代码时用 `ts_code` 过滤
- 工具返回含【完整数据下载】链接时：必须输出查询结果规模、统计摘要、预览表格、下载链接、图片 markdown；禁止补全或推测预览中未出现的数据，覆盖不到的内容引导用户看下载链接
- 工具返回不含下载链接时：原样输出全部内容（含图片 markdown），不得只总结表格或省略图片
- 查询结果只有一行时，原样输出该行，不追加统计信息或分析说明

## 7. 运行配置

| 配置项 | 位置 | 说明 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 环境变量 | DashScope API Key，代码启动时读取 |
| `DB_PATH` / `DB_URL` | 文件顶部常量 | SQLite 数据库路径与连接串 |
| `MAX_PREVIEW_ROWS` / `MAX_PREVIEW_COLS` | 文件顶部常量 | 进入 LLM 上下文的预览行数、列数 |
| `MAX_SESSION_CACHE` | 文件顶部常量 | DataFrame 会话缓存上限（LRU） |
| `EXPORT_DIR` | 文件顶部常量 | 完整数据 CSV 输出目录 |
| `llm_cfg.model` | `init_agent_service()` | 当前 `qwen3.8-max-0902`，超时 30 秒，重试 3 次 |
| `llm_cfg.model_type` / `model_server` | `init_agent_service()` | 固定为 `oai` + DashScope OpenAI 兼容端点，qwen3.x 必须走该端点 |
| `llm_cfg.generate_cfg.max_input_tokens` | `init_agent_service()` | 30000，主动压低以触发预览截断 |
| 启动模式 | `__main__` | 默认 `app_gui()`，终端模式 `app_tui()` 需手动切换 |

模型名与端点必须匹配：qwen3.x 走 OpenAI 兼容端点；若换回 `qwen-max` 等经典端点模型，需删除 `model_type` 与 `model_server`，否则启动正常但提问报 `Throttling.RateQuota` 或 url error。

## 8. 交互模式

- `app_gui()`：基于 qwen-agent `WebUI` 启动 Web 聊天界面，内置 3 条示例问题
- `app_tui()`：命令行连续对话，支持可选文件输入，失败时提示重试

## 9. 已知约束

- 进入上下文的只有统计摘要与预览，完整数据落在 `exports/`；用户需要全量数据时依靠下载链接
- 图表只反映查询结果本身，不做额外聚合或二次计算
- 多个数值列做透视时会产生元组列名，图表按列叠加呈现，适合趋势观察而非精确对比
- 依赖中文字体（SimHei / Microsoft YaHei 等），缺失时图中中文可能显示为方块
- 每次提问包含 Memory、工具调用、最终回答等多轮 LLM 请求，账号限流时需降低并发或更换模型
