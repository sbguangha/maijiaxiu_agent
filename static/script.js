const previewBtn = document.getElementById('previewBtn');
const generateBtn = document.getElementById('generateBtn');
const statusBadge = document.getElementById('statusBadge');
const progressArea = document.getElementById('progressArea');
const progressBar = document.getElementById('progressBar');
const progressText = document.getElementById('progressText');
const tableArea = document.getElementById('tableArea');

let previewRows = [];

previewBtn.addEventListener('click', loadPreview);
generateBtn.addEventListener('click', startBatchGenerate);

async function loadPreview() {
  previewBtn.disabled = true;
  previewBtn.textContent = '读取中...';
  generateBtn.disabled = true;

  try {
    const resp = await fetch('/batch-generate/preview');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    if (data.error) throw new Error(data.error);

    previewRows = data.rows || [];
    renderPreviewTable(previewRows);

    if (previewRows.length > 0) {
      generateBtn.disabled = false;
    }
  } catch (e) {
    tableArea.innerHTML = `<div class="empty-state">读取失败: ${escapeHtml(e.message)}</div>`;
  } finally {
    previewBtn.disabled = false;
    previewBtn.textContent = '读取需求表';
  }
}

function renderPreviewTable(rows) {
  if (!rows.length) {
    tableArea.innerHTML = '<div class="empty-state">需求表为空</div>';
    return;
  }

  let html = '<div class="table-scroll"><table class="data-table">';
  html += `<thead><tr>
    <th>#</th>
    <th>商品标题</th>
    <th>平铺图</th>
    <th>评价数</th>
    <th>晒图数</th>
    <th>状态</th>
    <th>备注</th>
  </tr></thead><tbody>`;

  rows.forEach((row, i) => {
    html += `<tr id="row-${row.record_id}">
      <td>${i + 1}</td>
      <td class="cell-title" title="${escapeHtml(row.product_title)}">${escapeHtml(row.product_title)}</td>
      <td>${row.has_image
        ? '<span class="has-image-yes">有</span>'
        : '<span class="has-image-no">无</span>'}</td>
      <td>${row.review_count}</td>
      <td>${row.image_count}</td>
      <td><span class="row-status pending">待处理</span></td>
      <td></td>
    </tr>`;
  });

  html += '</tbody></table></div>';
  tableArea.innerHTML = html;
}

async function startBatchGenerate() {
  previewBtn.disabled = true;
  generateBtn.disabled = true;
  progressArea.classList.remove('hidden');
  progressBar.style.width = '0%';
  progressText.textContent = '正在批量生成，请勿关闭页面...';

  setBadge('running', '生成中...');

  try {
    const resp = await fetch('/batch-generate', { method: 'POST' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    if (data.error) throw new Error(data.error);

    const results = data.results || [];
    const total = data.total || 0;
    const successCount = data.success_count || 0;

    progressBar.style.width = '100%';
    progressText.textContent = `完成: ${successCount}/${total} 成功`;

    updateTableWithResults(results);
    setBadge('completed', `${successCount}/${total} 成功`);
  } catch (e) {
    progressText.textContent = `批量生成失败: ${e.message}`;
    setBadge('error', '失败');
  } finally {
    previewBtn.disabled = false;
    generateBtn.disabled = false;
  }
}

function updateTableWithResults(results) {
  results.forEach((r) => {
    const tr = document.getElementById(`row-${r.record_id}`);
    if (!tr) return;

    const cells = tr.querySelectorAll('td');
    const statusCell = cells[5];
    const noteCell = cells[6];

    if (r.status === 'success') {
      statusCell.innerHTML = '<span class="row-status success">成功</span>';
      const parts = [];
      if (r.queued_task_id) parts.push('已入队');
      if (r.image_urls && r.image_urls.length) parts.push(`${r.image_urls.length}张晒图`);
      noteCell.textContent = parts.join(', ') || '完成';
    } else {
      statusCell.innerHTML = '<span class="row-status error">失败</span>';
      noteCell.innerHTML = `<span class="cell-error" title="${escapeHtml(r.error || '')}">${escapeHtml(r.error || '未知错误')}</span>`;
    }
  });
}

function setBadge(type, text) {
  statusBadge.className = `status-badge ${type}`;
  statusBadge.textContent = text;
  statusBadge.classList.remove('hidden');
}

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
