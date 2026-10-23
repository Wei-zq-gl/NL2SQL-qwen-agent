/* 股票 BI 助手前端逻辑：会话列表、SSE 流式对话、Markdown 渲染 */

const appEl = document.getElementById('app');
const mainEl = document.getElementById('main');
const chatArea = document.getElementById('chatArea');
const messagesEl = document.getElementById('messages');
const historyEl = document.getElementById('history');
const heroEl = document.getElementById('hero');
const inputEl = document.getElementById('input');
const composerEl = document.getElementById('composer');
const sendBtn = document.getElementById('sendBtn');
const suggestionBtn = document.getElementById('suggestionBtn');
const suggestPop = document.getElementById('suggestPop');

const ASSISTANT_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none">' +
  '<path d="M5 19V9m4.5 10V5m4.5 14v-7m4.5 7V3" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>' +
  '</svg>';

const state = {
  sessionId: null,
  streaming: false,
  assistantText: '',
};

/* ===== Markdown 渲染 ===== */

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderInline(text) {
  return escapeHtml(text)
    .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, '<img alt="$1" src="$2">')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
}

function splitRow(line) {
  let text = line.trim();
  if (text.startsWith('|')) text = text.slice(1);
  if (text.endsWith('|')) text = text.slice(0, -1);
  return text.split('|').map(cell => cell.trim());
}

function isTableDivider(line) {
  return /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes('-');
}

function isBlockStart(line, nextLine) {
  return line.trim().startsWith('```') ||
    /^#{1,6}\s+/.test(line) ||
    /^\s*[-*+]\s+/.test(line) ||
    /^\s*\d+\.\s+/.test(line) ||
    /^>\s?/.test(line) ||
    /^\s*([-*_])\1{2,}\s*$/.test(line) ||
    (line.includes('|') && Boolean(nextLine) && isTableDivider(nextLine));
}

/* 将助手返回的 markdown 转成 HTML，覆盖表格、图片、代码块等常用语法 */
function renderMarkdown(raw) {
  const lines = String(raw).replace(/\r\n/g, '\n').split('\n');
  let html = '';
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }

    // 代码块
    if (line.trim().startsWith('```')) {
      const body = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        body.push(lines[i]);
        i++;
      }
      i++;
      html += '<pre><code>' + escapeHtml(body.join('\n')) + '</code></pre>';
      continue;
    }

    // 表格
    if (line.includes('|') && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
      const header = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      html += '<div class="table-wrap"><table><thead><tr>' +
        header.map(cell => '<th>' + renderInline(cell) + '</th>').join('') +
        '</tr></thead><tbody>' +
        rows.map(row => '<tr>' +
          header.map((_, index) => '<td>' + renderInline(row[index] || '') + '</td>').join('') +
          '</tr>').join('') +
        '</tbody></table></div>';
      continue;
    }

    // 标题（降级到 h3 起，避免页面出现超大标题）
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 6);
      html += '<h' + level + '>' + renderInline(heading[2]) + '</h' + level + '>';
      i++;
      continue;
    }

    // 无序列表
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ''));
        i++;
      }
      html += '<ul>' + items.map(item => '<li>' + renderInline(item) + '</li>').join('') + '</ul>';
      continue;
    }

    // 有序列表
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ''));
        i++;
      }
      html += '<ol>' + items.map(item => '<li>' + renderInline(item) + '</li>').join('') + '</ol>';
      continue;
    }

    // 引用
    if (/^>\s?/.test(line)) {
      const items = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        items.push(lines[i].replace(/^>\s?/, ''));
        i++;
      }
      html += '<blockquote>' + renderInline(items.join(' ')) + '</blockquote>';
      continue;
    }

    // 分隔线
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      html += '<hr>';
      i++;
      continue;
    }

    // 普通段落
    const paragraph = [];
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i], lines[i + 1])) {
      paragraph.push(lines[i]);
      i++;
    }
    if (paragraph.length) {
      html += '<p>' + paragraph.map(item => renderInline(item)).join('<br>') + '</p>';
    } else {
      html += '<p>' + renderInline(line) + '</p>';
      i++;
    }
  }

  return html;
}

/* ===== 消息渲染 ===== */

function scrollToBottom() {
  chatArea.scrollTop = chatArea.scrollHeight;
}

function showHero(visible) {
  heroEl.style.display = visible ? 'flex' : 'none';
  mainEl.classList.toggle('empty', visible);
  mainEl.classList.toggle('chatting', !visible);
}

function appendUser(text) {
  const row = document.createElement('div');
  row.className = 'msg-user';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  showHero(false);
  scrollToBottom();
}

function createAssistantBlock() {
  const row = document.createElement('div');
  row.className = 'msg-assistant';
  const avatar = document.createElement('div');
  avatar.className = 'msg-avatar';
  avatar.innerHTML = ASSISTANT_ICON;
  const content = document.createElement('div');
  content.className = 'msg-content';
  row.append(avatar, content);
  messagesEl.appendChild(row);
  return content;
}

function appendAssistant(text) {
  const content = createAssistantBlock();
  content.innerHTML = text ? renderMarkdown(text) : '<span class="typing"><i></i><i></i><i></i></span>';
  scrollToBottom();
  return content;
}

/* ===== 会话列表 ===== */

async function loadSessions() {
  const response = await fetch('/api/sessions');
  if (!response.ok) return;
  const groups = await response.json();
  historyEl.innerHTML = '';

  groups.forEach(group => {
    const groupEl = document.createElement('div');
    groupEl.className = 'history-group';

    const titleEl = document.createElement('div');
    titleEl.className = 'history-title';
    titleEl.textContent = group.title;
    groupEl.appendChild(titleEl);

    group.items.forEach(item => {
      const row = document.createElement('div');
      row.className = 'history-item' + (item.id === state.sessionId ? ' active' : '');
      row.title = item.title;

      const label = document.createElement('span');
      label.className = 'item-title';
      label.textContent = item.title;

      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'item-del';
      delBtn.textContent = '×';
      delBtn.title = '删除对话';
      delBtn.addEventListener('click', async event => {
        event.stopPropagation();
        await fetch('/api/sessions/' + item.id, { method: 'DELETE' });
        if (state.sessionId === item.id) startNewChat();
        await loadSessions();
      });

      row.append(label, delBtn);
      row.addEventListener('click', () => openSession(item.id));
      groupEl.appendChild(row);
    });

    historyEl.appendChild(groupEl);
  });
}

async function openSession(sessionId) {
  if (state.streaming || sessionId === state.sessionId) return;
  const response = await fetch('/api/sessions/' + sessionId);
  if (!response.ok) return;
  const data = await response.json();

  state.sessionId = sessionId;
  state.assistantText = '';
  messagesEl.innerHTML = '';
  data.turns.forEach(turn => {
    if (turn.role === 'user') {
      appendUser(turn.content);
    } else {
      appendAssistant(turn.content);
    }
  });
  showHero(data.turns.length === 0);
  await loadSessions();
  scrollToBottom();
}

function startNewChat() {
  if (state.streaming) return;
  state.sessionId = null;
  state.assistantText = '';
  messagesEl.innerHTML = '';
  showHero(true);
  inputEl.value = '';
  resizeInput();
  updateSendState();
  document.querySelectorAll('.history-item.active').forEach(item => item.classList.remove('active'));
  inputEl.focus();
}

/* ===== 对话请求 ===== */

function handleEvent(event, contentEl) {
  if (event.type === 'session') {
    state.sessionId = event.session_id;
  } else if (event.type === 'delta') {
    state.assistantText += event.delta;
    contentEl.innerHTML = renderMarkdown(state.assistantText);
    scrollToBottom();
  } else if (event.type === 'content') {
    state.assistantText = event.content;
    contentEl.innerHTML = renderMarkdown(state.assistantText);
    scrollToBottom();
  } else if (event.type === 'error') {
    contentEl.innerHTML = '<span class="msg-error">' + escapeHtml(event.message) + '</span>';
  } else if (event.type === 'done' && !state.assistantText) {
    contentEl.innerHTML = '<span class="msg-error">助手未返回内容，请重试</span>';
  }
}

async function sendMessage(text) {
  if (state.streaming || !text.trim()) return;

  state.streaming = true;
  state.assistantText = '';
  updateSendState();
  inputEl.value = '';
  resizeInput();

  appendUser(text);
  const contentEl = appendAssistant('');

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: state.sessionId }),
    });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || '请求失败');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let index;
      while ((index = buffer.indexOf('\n\n')) !== -1) {
        const chunk = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        const line = chunk.split('\n').find(item => item.startsWith('data: '));
        if (!line) continue;
        handleEvent(JSON.parse(line.slice(6)), contentEl);
      }
    }
  } catch (error) {
    contentEl.innerHTML = '<span class="msg-error">' + escapeHtml(error.message || String(error)) + '</span>';
  } finally {
    state.streaming = false;
    updateSendState();
    await loadSessions();
    scrollToBottom();
  }
}

/* ===== 输入框交互 ===== */

function resizeInput() {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
}

function updateSendState() {
  sendBtn.disabled = state.streaming || !inputEl.value.trim();
}

inputEl.addEventListener('input', () => {
  resizeInput();
  updateSendState();
});

inputEl.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage(inputEl.value);
  }
});

composerEl.addEventListener('submit', event => {
  event.preventDefault();
  sendMessage(inputEl.value);
});

suggestionBtn.addEventListener('click', event => {
  event.stopPropagation();
  suggestPop.hidden = !suggestPop.hidden;
});

document.addEventListener('click', event => {
  if (!suggestPop.hidden && !suggestPop.contains(event.target) && event.target !== suggestionBtn) {
    suggestPop.hidden = true;
  }
});

(window.SUGGESTIONS || []).forEach(question => {
  const item = document.createElement('button');
  item.type = 'button';
  item.className = 'suggest-item';
  item.textContent = question;
  item.addEventListener('click', () => {
    inputEl.value = question;
    suggestPop.hidden = true;
    resizeInput();
    updateSendState();
    inputEl.focus();
  });
  suggestPop.appendChild(item);
});

document.getElementById('collapseBtn').addEventListener('click', () => {
  appEl.classList.add('collapsed');
});

document.getElementById('expandBtn').addEventListener('click', () => {
  appEl.classList.remove('collapsed');
});

document.getElementById('newChatBtn').addEventListener('click', () => {
  startNewChat();
  loadSessions();
});

/* ===== 初始化 ===== */

showHero(true);
resizeInput();
updateSendState();
loadSessions();
inputEl.focus();
