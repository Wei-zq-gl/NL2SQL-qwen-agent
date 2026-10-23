# -*- coding: utf-8 -*-
"""股票行情助手 Flask 图形界面

界面参考 DeepSeek 聊天页：左侧会话历史，右侧对话区。
Agent 初始化、SQL 查询、图表生成全部复用 assistant_stock_bot.py，本文件只做 Web 层封装。
"""

import json
import os
import threading
import time
import uuid
from datetime import date, timedelta

from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context

from assistant_stock_bot import init_agent_service

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_DIR = os.path.join(BASE_DIR, 'image_show')

# 首页示例问题
SUGGESTIONS = [
    '查询2025年全年贵州茅台的收盘价走势',
    '统计2025年4月广发证券的日均成交量',
    '对比2025年中芯国际和贵州茅台的涨跌幅',
]

app = Flask(__name__)

# Agent 全局单例：初始化会拉起 MCP 子进程，只做一次
_agent = None
_agent_lock = threading.Lock()

# 内存会话表：session_id -> 会话数据
_sessions = {}
_sessions_lock = threading.Lock()


def get_agent():
    """懒加载 Agent，避免每个请求都重新初始化 MCP 与模型配置"""
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                _agent = init_agent_service()
    return _agent


def create_session():
    """创建会话，首次提问前不会出现在历史列表里"""
    session = {
        'id': uuid.uuid4().hex,
        'title': '新对话',
        'created_at': time.time(),
        'messages': [],  # 传给 Agent 的完整消息
        'turns': [],  # 用于界面回显的 user/assistant 文本
    }
    with _sessions_lock:
        _sessions[session['id']] = session
    return session


def message_text(message):
    """从 Agent 消息中提取纯文本，兼容字符串与分段内容两种格式"""
    content = message.get('content', '')
    if isinstance(content, str):
        return content
    texts = []
    for part in content:
        if isinstance(part, dict) and part.get('text'):
            texts.append(part['text'])
    return ''.join(texts)


def assistant_text(messages):
    """取最后一条 assistant 消息的文本"""
    for message in reversed(messages):
        if message.get('role') == 'assistant':
            return message_text(message)
    return ''


def sse(payload):
    """封装一条 SSE 事件"""
    return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'


@app.route('/')
def index():
    return render_template('index.html', suggestions=SUGGESTIONS)


@app.route('/image_show/<path:filename>')
def chart_image(filename):
    """提供 exc_sql 生成的可视化图片"""
    return send_from_directory(IMAGE_DIR, filename)


@app.route('/api/sessions')
def api_sessions():
    """按日期分组返回历史会话"""
    with _sessions_lock:
        items = [s for s in _sessions.values() if s['turns']]
    items.sort(key=lambda s: s['created_at'], reverse=True)

    today = date.today()
    groups = {'今天': [], '昨天': [], '更早': []}
    for session in items:
        created = date.fromtimestamp(session['created_at'])
        if created == today:
            label = '今天'
        elif created == today - timedelta(days=1):
            label = '昨天'
        else:
            label = '更早'
        groups[label].append({'id': session['id'], 'title': session['title']})

    return jsonify([{'title': title, 'items': groups[title]} for title in groups if groups[title]])


@app.route('/api/sessions/<session_id>')
def api_session_detail(session_id):
    session = _sessions.get(session_id)
    if session is None:
        return jsonify({'error': '会话不存在'}), 404
    return jsonify({'id': session['id'], 'title': session['title'], 'turns': session['turns']})


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def api_delete_session(session_id):
    with _sessions_lock:
        _sessions.pop(session_id, None)
    return jsonify({'ok': True})


@app.route('/api/chat', methods=['POST'])
def api_chat():
    payload = request.get_json(silent=True) or {}
    text = (payload.get('message') or '').strip()
    if not text:
        return jsonify({'error': '消息不能为空'}), 400

    session = _sessions.get(payload.get('session_id') or '')
    if session is None:
        session = create_session()

    if session['title'] == '新对话':
        session['title'] = text if len(text) <= 20 else text[:20] + '…'

    session['turns'].append({'role': 'user', 'content': text})

    def generate():
        yield sse({'type': 'session', 'session_id': session['id'], 'title': session['title']})
        try:
            bot = get_agent()
        except Exception as exc:
            yield sse({'type': 'error', 'message': f'助手初始化失败：{exc}'})
            return

        messages = list(session['messages'])
        messages.append({'role': 'user', 'content': text})
        final_messages = messages
        previous = ''

        try:
            for response in bot.run(messages):
                final_messages = response
                content = assistant_text(response)
                if not content or content == previous:
                    continue
                if content.startswith(previous):
                    yield sse({'type': 'delta', 'delta': content[len(previous):]})
                else:
                    yield sse({'type': 'content', 'content': content})
                previous = content
        except Exception as exc:
            yield sse({'type': 'error', 'message': f'查询失败：{exc}'})
            return

        session['messages'] = final_messages
        if previous:
            session['turns'].append({'role': 'assistant', 'content': previous})
        yield sse({'type': 'done'})

    headers = {
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
    }
    return Response(stream_with_context(generate()), mimetype='text/event-stream', headers=headers)


if __name__ == '__main__':
    print('启动地址：http://127.0.0.1:5000')
    app.run(host='127.0.0.1', port=5000, threaded=True, debug=False)
