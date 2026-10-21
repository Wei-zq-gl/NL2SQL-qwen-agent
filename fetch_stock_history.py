# -*- coding: utf-8 -*-
"""
使用 tushare 获取指定股票的历史价格数据，并保存到 Excel 单个工作表中。
目标股票：贵州茅台、五粮液、广发证券、中芯国际
时间范围：2020-01-01 至今
排序方式：按交易日从小到大排序
"""

import os
import sqlite3
import sys
from datetime import datetime

import pandas as pd
import tushare as ts


def get_tushare_token():
    """从环境变量 TUSHARE_TOKEN 获取 tushare token。"""
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError(
            "未找到环境变量 TUSHARE_TOKEN，请先设置该环境变量后再运行脚本。"
        )
    return token


def save_to_sqlite(result, db_path, schema_path):
    """将行情数据写入 SQLite 数据库。

    result      : 已合并、排序好的行情 DataFrame
    db_path     : SQLite 数据库文件路径
    schema_path : 建表语句所在的 SQL 文件路径
    """
    # 读取建表语句，保证表结构与 schema.sql 保持一致
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    # sqlite3 的 with 语句只负责事务提交/回滚，不会关闭连接，故显式 close
    conn = sqlite3.connect(db_path)
    try:
        # 执行建表语句（IF NOT EXISTS，重复运行不会重建表）
        conn.executescript(schema_sql)
        # 清空历史数据，避免重复运行产生重复记录
        conn.execute("DELETE FROM stock_daily")
        # 写入本次获取的最新数据
        result.to_sql("stock_daily", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()

    print(f"数据已写入 SQLite 数据库：{db_path}")


def main():
    # 目标股票代码与名称映射
    stock_map = {
        "600519.SH": "贵州茅台",
        "000858.SZ": "五粮液",
        "000776.SZ": "广发证券",
        "688981.SH": "中芯国际",
    }

    # 时间范围
    start_date = "20200101"
    end_date = datetime.now().strftime("%Y%m%d")

    # 初始化 tushare
    token = get_tushare_token()
    ts.set_token(token)
    pro = ts.pro_api()

    # 逐只股票拉取数据
    df_list = []
    for ts_code, name in stock_map.items():
        print(f"正在获取 {name}({ts_code}) 的历史行情 ...")
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            print(f"  警告：{name} 未获取到数据，已跳过。")
            continue

        # 增加股票名称列，便于在同一个工作表中区分
        df.insert(1, "name", name)

        # 将 trade_date 转为 datetime，便于统一排序
        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
        df_list.append(df)
        print(f"  已获取 {len(df)} 条记录。")

    if not df_list:
        raise RuntimeError("所有股票均未获取到数据，请检查 token 或网络。")

    # 合并所有数据
    result = pd.concat(df_list, ignore_index=True)

    # 按交易日从小到大排序，同一交易日内按股票代码排序
    result = result.sort_values(by=["trade_date", "ts_code"], ascending=[True, True])

    # 恢复 trade_date 为字符串格式 YYYY-MM-DD，提升可读性
    result["trade_date"] = result["trade_date"].dt.strftime("%Y-%m-%d")

    # 输出目录
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # 保存到 Excel 单个工作表
    output_file = os.path.join(base_dir, "stock_history.xlsx")
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        result.to_excel(writer, sheet_name="历史行情", index=False)

    print(f"\n数据已保存到：{output_file}")
    print(f"共 {len(result)} 条记录，{result['ts_code'].nunique()} 只股票。")

    # 保存到 SQLite 数据库
    db_path = os.path.join(base_dir, "stock_history.db")
    schema_path = os.path.join(base_dir, "schema.sql")
    save_to_sqlite(result, db_path, schema_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"运行出错：{e}", file=sys.stderr)
        sys.exit(1)
