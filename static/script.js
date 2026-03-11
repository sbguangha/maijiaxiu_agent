// ===== DOM 元素 =====
const chatLog = document.getElementById('chatLog');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');
const uploadBtn = document.getElementById('uploadBtn');
const imageInput = document.getElementById('imageInput');
const imagePreview = document.getElementById('imagePreview');
const previewImg = document.getElementById('previewImg');
const removeImgBtn = document.getElementById('removeImgBtn');

// ===== 状态：当前附带的图片文件 =====
let attachedFile = null;

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
    contentDiv.innerHTML = content;
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

function updateTypingText(text) {
  const el = document.getElementById('typing');
  if (el) {
    const textSpan = el.querySelector('.typing-text');
    if (textSpan) {
      textSpan.textContent = text;
    }
  }
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

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>');
}

// ===== 图片附件管理 =====
function attachImage(file) {
  attachedFile = file;
  const reader = new FileReader();
  reader.onload = function (e) {
    previewImg.src = e.target.result;
    imagePreview.style.display = 'flex';
  };
  reader.readAsDataURL(file);
}

function removeImage() {
  attachedFile = null;
  previewImg.src = '';
  imagePreview.style.display = 'none';
  imageInput.value = '';
}

// ===== 统一发送（文字 / 文字+图片） =====
async function sendMessage() {
  const text = userInput.value.trim();
  const hasImage = !!attachedFile;

  // 至少要有文字或图片
  if (!text && !hasImage) return;

  // ---- 构建用户消息展示 ----
  let userHtml = '';
  if (hasImage) {
    userHtml += `<img src="${previewImg.src}" class="preview-img" alt="商品白底图">`;
    if (text) userHtml += '<br>';
  }
  if (text) {
    userHtml += escapeHtml(text);
  }
  addMessage(userHtml, 'user');

  // 清空输入区
  const currentText = text;
  const currentFile = attachedFile;
  userInput.value = '';
  userInput.style.height = 'auto';
  removeImage();

  // 禁用输入
  setInputsDisabled(true);

  try {
    if (hasImage) {
      // ===== 有图片：走合并接口 =====
      showTyping('正在生成评价…');

      const formData = new FormData();
      formData.append('file', currentFile);
      formData.append('user_input', currentText);

      const resp = await fetch('/chat-with-image', {
        method: 'POST',
        body: formData
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      removeTyping();

      // 显示评价文字
      if (data.reply) {
        addMessage(escapeHtml(data.reply), 'bot');
      }

      // 显示晒图结果
      if (data.image_result) {
        let imgHtml = '';
        const ir = data.image_result;

        if (ir.status === 'success' || ir.status === 'partial') {
          imgHtml += `<strong>${ir.message}</strong><br><br>`;

          if (ir.prompts && ir.prompts.length > 0) {
            imgHtml += '<strong>🎨 场景描述：</strong><br>';
            ir.prompts.forEach((p, i) => {
              imgHtml += `${i + 1}. ${escapeHtml(p)}<br>`;
            });
            imgHtml += '<br>';
          }

          if (ir.image_urls && ir.image_urls.length > 0) {
            imgHtml += '<strong>🖼️ 生成的买家秀晒图：</strong><br>';
            imgHtml += '<div class="generated-images">';
            ir.image_urls.forEach((url, i) => {
              imgHtml += `<img src="${url}" alt="场景${i + 1}" class="generated-img" loading="lazy">`;
            });
            imgHtml += '</div>';
          }
        } else {
          imgHtml = `❌ ${ir.message || '晒图生成失败'}`;
        }

        addMessage(imgHtml, 'bot');
      }

    } else {
      // ===== 纯文字：走原来的 /chat =====
      showTyping('正在生成评价…');

      const resp = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_input: currentText })
      });

      removeTyping();

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      const reply = data.reply || '抱歉，生成失败了。';
      addMessage(escapeHtml(reply), 'bot');
    }

  } catch (e) {
    removeTyping();
    addMessage('❌ 请求出错：' + e.message + '<br>可能是 API 过载，请等几秒再试。', 'bot');
    console.error(e);
  } finally {
    setInputsDisabled(false);
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

// 📸 按钮：选择文件（不发送）
uploadBtn.addEventListener('click', () => {
  imageInput.click();
});

// 文件选中后：只显示预览，不发送
imageInput.addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (file) {
    attachImage(file);
  }
});

// 移除已附带的图片
removeImgBtn.addEventListener('click', () => {
  removeImage();
});
