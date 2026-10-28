# -*- coding: utf-8 -*-
"""
股票行情查询助手（基于 qwen-agent）

数据来源：本地 SQLite 数据库 stock_history.db 中的 stock_daily 表
（由 fetch_stock_history.py + schema.sql 生成）

功能：用户用自然语言提问 -> 助手生成 SQL -> exc_sql 工具查询 SQLite
      -> 返回 markdown 表格 + 柱状图

参考：assistant_ticket_bot-3.py
"""

import os
import time

import dashscope
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from qwen_agent.agents import Assistant
from qwen_agent.gui import WebUI
from qwen_agent.tools.base import BaseTool, register_tool

import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端，避免 WebUI 环境下的绘图报错
import matplotlib.pyplot as plt

import hashlib
from collections import OrderedDict

# ====== 冰山式上下文治理配置 ======
MAX_PREVIEW_ROWS = 80      # 进入 LLM 上下文的最大预览行数
MAX_PREVIEW_COLS = 12      # 进入 LLM 上下文的最大预览列数
MAX_SESSION_CACHE = 100    # _last_df_dict 的最大缓存会话数（LRU）
EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'exports')

# 解决中文显示问题
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

# 配置 DashScope
dashscope.api_key = os.getenv('DASHSCOPE_API_KEY', '')  # 从环境变量获取 API Key
dashscope.timeout = 30

# SQLite 数据库路径（与 fetch_stock_history.py 输出保持一致）
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_history.db')
DB_URL = 'sqlite:///' + DB_PATH.replace('\\', '/')

# ====== 股票助手 system prompt ======
system_prompt = """我是股票行情查询助手，以下是股票日线行情表 stock_daily 的字段说明，我可以编写对应的 SQL 对数据进行查询。

-- 股票日线行情表
CREATE TABLE stock_daily (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,  -- 自增主键
    ts_code     TEXT    NOT NULL,                   -- 股票代码（如 600519.SH）
    name        TEXT,                               -- 股票名称（如 贵州茅台）
    trade_date  TEXT    NOT NULL,                   -- 交易日期（YYYY-MM-DD）
    open        REAL,                               -- 开盘价
    high        REAL,                               -- 最高价
    low         REAL,                               -- 最低价
    close       REAL,                               -- 收盘价
    pre_close   REAL,                               -- 昨收价
    change      REAL,                               -- 涨跌额
    pct_chg     REAL,                               -- 涨跌幅（%）
    vol         REAL,                               -- 成交量（手）
    amount      REAL,                               -- 成交额（千元）
    UNIQUE (ts_code, trade_date)                    -- 同一股票同一交易日唯一
);

数据说明：
- 表中包含 4 只股票：贵州茅台(600519.SH)、五粮液(000858.SZ)、广发证券(000776.SZ)、中芯国际(688981.SH)。
- 数据时间范围为 2020-01-01 至今。
- trade_date 为文本类型，格式为 YYYY-MM-DD，日期比较和排序可直接按字符串处理。
- 数据库为 SQLite，生成 SQL 时请使用 SQLite 语法，表名统一为 stock_daily。
- 当用户提到股票名称时，建议用 name 字段过滤；当用户提到代码时，使用 ts_code 字段过滤。

我将回答用户关于股票行情相关的问题。

每当 exc_sql 工具返回内容时，请遵循以下规则：
- 若工具返回中包含【完整数据下载】链接，说明结果已被预览截断。
  此时你必须输出：查询结果规模、统计摘要、预览表格、【完整数据下载】链接、图片 markdown。
  严禁尝试补全或复述完整数据，也不要凭空推测未在预览中出现的数据。
  若用户询问的内容不在预览、统计摘要或图表覆盖范围内，请明确告知
  “该数据未在当前展示范围内，请通过下载链接查看完整数据”，不要编造数值。
- 若工具返回中不包含【完整数据下载】链接，说明结果完整。
  此时应原样输出工具返回的全部内容（包括图片 markdown），不要只总结表格，也不要省略图片。
- 当查询结果只有一行数据时，原样输出该行即可，不要额外输出统计信息或分析说明。
"""

functions_desc = [
    {
        "name": "exc_sql",
        "description": "对于生成的SQL，进行SQL查询",
        "parameters": {
            "type": "object",
            "properties": {
                "sql_input": {
                    "type": "string",
                    "description": "生成的SQL语句",
                }
            },
            "required": ["sql_input"],
        },
    },
]

# ====== 会话隔离 DataFrame 存储（LRU 限容，避免内存泄漏） ======
_last_df_dict = OrderedDict()


def get_session_id(kwargs):
    """基于 messages 内容生成稳定会话指纹，避免 id(messages) 回收复用导致串会话。"""
    messages = kwargs.get('messages')
    if not messages:
        return None
    first_user = None
    for m in messages:
        if m.get('role') == 'user':
            first_user = m
            break
    if first_user is None:
        return None
    content = first_user.get('content', '')
    if isinstance(content, str):
        seed = content
    else:
        seed = ''.join(p.get('text', '') for p in content if isinstance(p, dict))
    return hashlib.md5(f"{seed}|{len(messages)}".encode('utf-8')).hexdigest()[:16]


def _store_df(session_id, df):
    """LRU 方式缓存 DataFrame，超过 MAX_SESSION_CACHE 时淘汰最旧会话。"""
    if not session_id:
        return
    _last_df_dict[session_id] = df
    _last_df_dict.move_to_end(session_id)
    while len(_last_df_dict) > MAX_SESSION_CACHE:
        _last_df_dict.popitem(last=False)


# ====== exc_sql 工具类实现 ======
@register_tool('exc_sql')
class ExcSQLTool(BaseTool):
    """
    SQL查询工具，对 SQLite 数据库执行传入的 SQL 语句并返回结果，并自动进行可视化。
    """
    description = '对于生成的SQL，进行SQL查询，并自动可视化'
    parameters = [{
        'name': 'sql_input',
        'type': 'string',
        'description': '生成的SQL语句',
        'required': True
    }]

    def call(self, params: str, **kwargs) -> str:
        import json

        session_id = get_session_id(kwargs)

        args = json.loads(params)
        sql_input = args['sql_input']
        print('sql_input=', sql_input)

        engine = create_engine(DB_URL)
        try:
            df_full = pd.read_sql(text(sql_input), engine)
        finally:
            engine.dispose()
        print('df=', df_full)

        _store_df(session_id, df_full)

        total_rows, total_cols = len(df_full), len(df_full.columns)

        # 空表 / 单行：保持原行为（不做统计、不画图）
        if total_rows <= 1:
            return df_full.to_markdown(index=False)

        # ① 完整数据落盘（水下 7/8）
        os.makedirs(EXPORT_DIR, exist_ok=True)
        ts = int(time.time() * 1000)
        csv_name = f'export_{ts}.csv'
        csv_path = os.path.join(EXPORT_DIR, csv_name)
        has_csv = False
        try:
            df_full.to_csv(csv_path, index=False, encoding='utf-8-sig')
            has_csv = True
        except Exception as exc:
            print('导出 CSV 失败:', exc)

        # ② 数值列统计摘要（供 LLM 推理）
        num_cols = df_full.select_dtypes(exclude='O').columns.tolist()
        stat_lines = []
        for col in num_cols[:6]:
            series = pd.to_numeric(df_full[col], errors='coerce').dropna()
            if len(series) == 0:
                continue
            stat_lines.append(
                f"- {col}: min={series.min():.2f}, max={series.max():.2f}, "
                f"mean={series.mean():.2f}, last={series.iloc[-1]:.2f}"
            )
        stats_md = "\n".join(stat_lines) if stat_lines else "（无数值列）"

        # ③ 预览（露出 1/8）
        preview = df_full.head(MAX_PREVIEW_ROWS).iloc[:, :MAX_PREVIEW_COLS]
        preview_md = preview.to_markdown(index=False)
        truncated = (total_rows > MAX_PREVIEW_ROWS) or (total_cols > MAX_PREVIEW_COLS)

        # ④ 图表用完整 df_full（质量不变）
        save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'image_show')
        os.makedirs(save_dir, exist_ok=True)
        filename = f'bar_{ts}.png'
        save_path = os.path.join(save_dir, filename)
        generate_chart_png(df_full, save_path)
        img_md = f'![图表](image_show/{filename})'

        # ⑤ 组装返回
        if truncated:
            header = (f"查询结果：{total_rows} 行 × {total_cols} 列"
                    f"（上下文仅展示前 {len(preview)} 行 × "
                    f"{min(total_cols, MAX_PREVIEW_COLS)} 列，完整数据见下载链接）")
        else:
            header = f"查询结果：{total_rows} 行 × {total_cols} 列（全部）"

        parts = [header, "", "【数值列统计】", stats_md, "", "【结果预览】", preview_md]
        if truncated and has_csv:
            parts += ["", f"【完整数据下载】[ {csv_name} ](exports/{csv_name})"]
        parts += ["", img_md]
        return "\n".join(parts)


# ========== 通用可视化函数 ==========
# 横坐标（分类）数量超过该阈值时，改用折线图，避免柱状图过密看不清
LINE_CHART_THRESHOLD = 20


def _safe_label(value):
    """对标签做安全处理，避免格式化字符问题。"""
    return str(value).replace('%', '%%').replace('{', '{{').replace('}', '}}')


def _set_thinned_xticks(ax, positions, labels, max_ticks=20):
    """横坐标较多时按较大步长显示部分刻度标签，避免标签重叠。

    步长从 10/15/20 这类易读的数值开始，横坐标越长步长越大，
    保证显示的标签数量不超过 max_ticks。
    """
    n = len(positions)
    step = 1000
    # n <= max_ticks * step 等价于按该步长取点后的标签数不超过 max_ticks
    for candidate in [10, 15, 20, 30, 50, 100, 200, 500, 1000]:
        if n <= max_ticks * candidate:
            step = candidate
            break
    idx = list(range(0, n, step))
    ax.set_xticks([positions[i] for i in idx])
    ax.set_xticklabels([_safe_label(labels[i]) for i in idx], rotation=45)


def generate_chart_png(df_sql, save_path):
    columns = df_sql.columns
    x = np.arange(len(df_sql))
    # 横坐标较多时使用折线图，较少时使用柱状图
    use_line = len(df_sql) > LINE_CHART_THRESHOLD
    # 获取object类型
    object_columns = df_sql.select_dtypes(include='O').columns.tolist()
    if columns[0] in object_columns:
        object_columns.remove(columns[0])
    num_columns = df_sql.select_dtypes(exclude='O').columns.tolist()
    if len(object_columns) > 0:
        # 对数据进行透视，以便为每个分类创建堆积柱状图/折线图
        pivot_df = df_sql.pivot_table(index=columns[0], columns=object_columns,
                                      values=num_columns,
                                      fill_value=0)
        fig, ax = plt.subplots(figsize=(10, 6))
        if use_line:
            # 横坐标较多，绘制折线图
            positions = np.arange(len(pivot_df))
            for col in pivot_df.columns:
                # 避免格式化字符问题，对可能包含特殊字符的字符串进行处理
                safe_label = _safe_label(col)
                ax.plot(positions, pivot_df[col].values, label=safe_label)
            _set_thinned_xticks(ax, positions, list(pivot_df.index))
        else:
            # 绘制堆积柱状图
            bottoms = None
            for col in pivot_df.columns:
                # 避免格式化字符问题，对可能包含特殊字符的字符串进行处理
                safe_label = _safe_label(col)
                ax.bar(pivot_df.index, pivot_df[col], bottom=bottoms, label=safe_label)
                if bottoms is None:
                    bottoms = pivot_df[col].copy()
                else:
                    bottoms += pivot_df[col]
    else:
        # 对x轴标签进行安全处理，避免格式化问题
        safe_xtick_labels = [_safe_label(val) for val in df_sql[columns[0]]]
        if use_line:
            # 横坐标较多，绘制折线图
            for column in columns[1:]:
                safe_label = _safe_label(column)
                plt.plot(x, df_sql[column].values, label=safe_label)
            _set_thinned_xticks(plt.gca(), x, safe_xtick_labels)
        else:
            bottom = np.zeros(len(df_sql))
            for column in columns[1:]:
                # 避免格式化字符问题
                safe_label = _safe_label(column)
                plt.bar(x, df_sql[column], bottom=bottom, label=safe_label)
                bottom += df_sql[column]
            plt.xticks(x, safe_xtick_labels)
    plt.legend()
    plt.title("查询结果可视化")
    # 对x轴标签也进行安全处理
    plt.xlabel(_safe_label(columns[0]))
    plt.ylabel("数值")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(save_path)
    plt.close()


# ====== 初始化股票查询助手服务 ======
def init_agent_service():
    """初始化股票查询助手服务"""
    llm_cfg = {
        'model': 'qwen3.8-max-0902',
        # DashScope 经典 text-generation 端点不服务 qwen3.x 系列，改走 OpenAI 兼容端点
        'model_type': 'oai',
        'model_server': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'api_key': os.getenv('DASHSCOPE_API_KEY', ''),
        'timeout': 30,
        'retry_count': 3,
        'generate_cfg': {
            'max_input_tokens': 30000,  # 主动压低，让 S1–S5 截断提前介入
        },
    }
    function_list=[
        'exc_sql',
        {
            "mcpServers": {
                "time": {
                    "command": "uvx",
                    "args": ["mcp-server-time", "--local-timezone=Asia/Shanghai"]
                }
            }
        },
    ]
    try:
        bot = Assistant(
            llm=llm_cfg,
            name='股票查询助手',
            description='股票行情查询与分析',
            system_message=system_prompt,
            function_list=function_list,
            files=['faq.txt'],
        )
        print("助手初始化成功！")
        return bot
    except Exception as e:
        print(f"助手初始化失败: {str(e)}")
        raise


def app_tui():
    """终端交互模式

    提供命令行交互界面，支持：
    - 连续对话
    - 文件输入
    - 实时响应
    """
    try:
        # 初始化助手
        bot = init_agent_service()

        # 对话历史
        messages = []
        while True:
            try:
                # 获取用户输入
                query = input('user question: ')
                # 获取可选的文件输入
                file = input('file url (press enter if no file): ').strip()

                # 输入验证
                if not query:
                    print('user question cannot be empty！')
                    continue

                # 构建消息
                if not file:
                    messages.append({'role': 'user', 'content': query})
                else:
                    messages.append({'role': 'user', 'content': [{'text': query}, {'file': file}]})

                print("正在处理您的请求...")
                # 运行助手并处理响应
                response = []
                for response in bot.run(messages):
                    print('bot response:', response)
                messages.extend(response)
            except Exception as e:
                print(f"处理请求时出错: {str(e)}")
                print("请重试或输入新的问题")
    except Exception as e:
        print(f"启动终端模式失败: {str(e)}")


def app_gui():
    """图形界面模式，提供 Web 图形界面"""
    try:
        print("正在启动 Web 界面...")
        # 初始化助手
        bot = init_agent_service()
        # 配置聊天界面，列举3个典型股票查询问题
        chatbot_config = {
            'prompt.suggestions': [
                '查询2025年全年贵州茅台的收盘价走势',
                '统计2025年4月广发证券的日均成交量',
                '对比2025年中芯国际和贵州茅台的涨跌幅',
            ]
        }
        print("Web 界面准备就绪，正在启动服务...")
        # 启动 Web 界面
        WebUI(
            bot,
            chatbot_config=chatbot_config
        ).run()
    except Exception as e:
        print(f"启动 Web 界面失败: {str(e)}")
        print("请检查网络连接和 API Key 配置")


if __name__ == '__main__':
    # 运行模式选择
    app_gui()          # 图形界面模式（默认）
    # app_tui()        # 终端交互模式（如需使用请解除注释）
