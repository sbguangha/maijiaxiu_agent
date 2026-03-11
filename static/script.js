// ===== DOM 元素 =====
const chatLog = document.getElementById('chatLog');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');
const uploadBtn = document.getElementById('uploadBtn');
const imageInput = document.getElementById('imageInput');

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

function showTyping(text) {
  const msgDiv = document.createElement('div');
  msgDiv.className = 'message bot';
  msgDiv.id = 'typing';

  const indicator = document.createElement('div');
  indicator.className = 'typing-indicator';
  indicator.innerHTML = `<span></span><span></span><span></span>${text ? `<span class="typing-text">${text}</span>` : ''}`;

  msgDiv.appendChild(indicator);
  chatLog.appendChild(msgDiv);
  scrollToBottom();
}

function removeTyping() {
  const el = document.getElementById('typing');
  if (el) el.remove();
}

function setInputsDisabled(disabled) {
  userInput.disabled = disabled;
  sendBtn.disabled = disabled;
  uploadBtn.disabled = disabled;
}

// ===== 发送文字消息（生成评价） =====
async function sendMessage() {
  const content = userInput.value.trim();
  if (!content) return;

  // 显示用户消息
  addMessage(content, 'user');
  userInput.value = '';
  userInput.style.height = 'auto';

  // 禁用输入
  setInputsDisabled(true);

  // 显示加载动画
  showTyping('正在生成评价…');

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
    setInputsDisabled(false);
    userInput.focus();
  }
}

// ===== 上传图片并生成买家秀配图 =====
async function uploadAndGenerate(file) {
  const productName = userInput.value.trim() || '服装商品';
  const sceneCount = 5;

  // 显示用户消息（带图片预览）
  const reader = new FileReader();
  reader.onload = function (e) {
    addMessage(
      `<img src="${e.target.result}" class="preview-img" alt="商品白底图"><br>📸 商品: ${productName}<br>正在生成买家秀配图...`,
      'user'
    );
  };
  reader.readAsDataURL(file);

  userInput.value = '';
  userInput.style.height = 'auto';

  // 禁用输入
  setInputsDisabled(true);

  // 显示加载动画
  showTyping('正在生成买家秀配图，约需 30-60 秒…');

  try {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('product_name', productName);
    formData.append('scene_count', '5');

    const resp = await fetch('/generate-lifestyle-images', {
      method: 'POST',
      body: formData
    });

    removeTyping();

    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }

    const data = await resp.json();

    // 构建结果 HTML
    let html = '';

    if (data.status === 'success' || data.status === 'partial') {
      html += `<strong>${data.message}</strong><br><br>`;

      // 显示场景 Prompt 列表
      if (data.prompts && data.prompts.length > 0) {
        html += '<strong>🎨 生成的场景描述：</strong><br>';
        data.prompts.forEach((p, i) => {
          html += `${i + 1}. ${p.replace(/</g, '&lt;').replace(/>/g, '&gt;')}<br>`;
        });
        html += '<br>';
      }

      // 显示生成的图片
      if (data.image_urls && data.image_urls.length > 0) {
        html += '<strong>🖼️ 生成的买家秀配图：</strong><br>';
        html += '<div class="generated-images">';
        data.image_urls.forEach((url, i) => {
          html += `<img src="${url}" alt="场景${i + 1}" class="generated-img" loading="lazy">`;
        });
        html += '</div>';
      }
    } else {
      html = `❌ ${data.message || '生成失败，请检查配置。'}`;
    }

    addMessage(html, 'bot');

  } catch (e) {
    removeTyping();
    addMessage('❌ 图片生成出错：' + e.message + '<br>请检查 DOUBAO_API_KEY 是否配置正确。', 'bot');
    console.error(e);
  } finally {
    setInputsDisabled(false);
    userInput.focus();
    // 清空文件选择，允许重复选择同一文件
    imageInput.value = '';
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

// 📸 上传按钮触发文件选择
uploadBtn.addEventListener('click', () => {
  imageInput.click();
});

// 文件选择后，先弹窗让用户输入商品名称，再触发生成
imageInput.addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;

  // 如果输入框里已经有文字，直接使用；否则弹窗询问
  let name = userInput.value.trim();
  if (!name) {
    name = prompt('请输入商品名称（如：白色V领针织开衫）：', '服装商品');
    if (!name) {
      imageInput.value = '';
      return; // 用户点了取消
    }
  }
  userInput.value = name;
  uploadAndGenerate(file);
});
