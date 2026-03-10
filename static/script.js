// ===== DOM 元素 =====
const chatLog = document.getElementById('chatLog');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');

// ===== 工具函数 =====
function scrollToBottom() {
  chatLog.scrollTop = chatLog.scrollHeight;
}

function addMessage(content, role) {
  const msgDiv = document.createElement('div');
  msgDiv.className = `message ${role}`;

  const contentDiv = document.createElement('div');
  contentDiv.className = 'msg-content';

  if (role === 'bot') {
    contentDiv.innerHTML = content;
  } else {
    contentDiv.textContent = content;
  }

  msgDiv.appendChild(contentDiv);
  chatLog.appendChild(msgDiv);
  scrollToBottom();
  return msgDiv;
}

function showTyping() {
  const msgDiv = document.createElement('div');
  msgDiv.className = 'message bot';
  msgDiv.id = 'typing';

  const indicator = document.createElement('div');
  indicator.className = 'typing-indicator';
  indicator.innerHTML = '<span></span><span></span><span></span>';

  msgDiv.appendChild(indicator);
  chatLog.appendChild(msgDiv);
  scrollToBottom();
}

function removeTyping() {
  const el = document.getElementById('typing');
  if (el) el.remove();
}

// ===== 发送消息 =====
async function sendMessage() {
  const content = userInput.value.trim();
  if (!content) return;

  // 显示用户消息
  addMessage(content, 'user');
  userInput.value = '';
  userInput.style.height = 'auto';

  // 禁用输入
  userInput.disabled = true;
  sendBtn.disabled = true;

  // 显示加载动画
  showTyping();

  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_input: content })
    });

    removeTyping();

    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }

    const data = await resp.json();

    // 将 Agent 返回的纯文本渲染为 HTML
    const reply = data.reply || '抱歉，生成失败了，请稍后重试。';
    // 把换行转为 <br>，保留格式
    const formatted = reply
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\n/g, '<br>');

    addMessage(formatted, 'bot');

  } catch (e) {
    removeTyping();
    addMessage('❌ 请求出错：' + e.message + '<br>可能是 API 过载，请等几秒再试。', 'bot');
    console.error(e);
  } finally {
    userInput.disabled = false;
    sendBtn.disabled = false;
    userInput.focus();
  }
}

// ===== 事件绑定 =====
sendBtn.addEventListener('click', sendMessage);

userInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// 自动调整输入框高度
userInput.addEventListener('input', () => {
  userInput.style.height = 'auto';
  userInput.style.height = Math.min(userInput.scrollHeight, 120) + 'px';
});
