-- 股票日线行情表建表语句（SQLite）
-- 数据来源：tushare pro.daily 接口
-- 字段说明参考 tushare 官方文档

CREATE TABLE IF NOT EXISTS stock_daily (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,  -- 自增主键，便于 BI 工具定位行
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
    UNIQUE (ts_code, trade_date)                    -- 同一股票同一交易日唯一，避免重复记录
);
