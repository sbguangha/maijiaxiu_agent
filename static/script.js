const previewBtn = document.getElementById('previewBtn');
const generateBtn = document.getElementById('generateBtn');
const statusBadge = document.getElementById('statusBadge');
const progressArea = document.getElementById('progressArea');
const progressBar = document.getElementById('progressBar');
const progressText = document.getElementById('progressText');
const tableArea = document.getElementById('tableArea');
const approvalArea = document.getElementById('approvalArea');

let previewRows = [];
let requireConfirmation = false;

previewBtn.addEventListener('click', loadPreview);
generateBtn.addEventListener('click', startBatchGenerate);
loadPendingApprovals();

async function loadPreview() {
  previewBtn.disabled = true;
  previewBtn.textContent = '读取中...';
  generateBtn.disabled = true;

  try {
    const resp = await fetch('/batch-generate/preview');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    if (data.error) throw new Error(data.error);

    requireConfirmation = !!data.require_confirmation;
    previewRows = data.rows || [];
    renderPreviewTable(previewRows);
    await loadPendingApprovals();

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
    const statusText = (row.processing_status || '').trim();
    const shouldProcess = !!row.should_process;
    const uiStatusText = shouldProcess ? (statusText || '待处理') : (statusText || '已处理');
    const statusClass = shouldProcess ? 'pending' : 'success';
    const noteText = shouldProcess ? '' : '本轮将跳过';

    html += `<tr id="row-${row.record_id}">
      <td>${i + 1}</td>
      <td class="cell-title" title="${escapeHtml(row.product_title)}">${escapeHtml(row.product_title)}</td>
      <td>${row.has_image
        ? '<span class="has-image-yes">有</span>'
        : '<span class="has-image-no">无</span>'}</td>
      <td>${row.review_count}</td>
      <td>${row.image_count}</td>
      <td><span class="row-status ${statusClass}">${escapeHtml(uiStatusText)}</span></td>
      <td>${escapeHtml(noteText)}</td>
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

    requireConfirmation = !!data.require_confirmation;
    const results = data.results || [];
    const total = data.total || 0;
    const successCount = data.success_count || 0;

    progressBar.style.width = '100%';
    progressText.textContent = `完成: ${successCount}/${total} 成功`;

    updateTableWithResults(results);
    if (requireConfirmation) {
      setBadge('completed', `${successCount}/${total} 已生成，待确认发送`);
      await loadPendingApprovals();
    } else {
      setBadge('completed', `${successCount}/${total} 成功`);
    }
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
      if (r.approval_id) {
        parts.push(`待确认(${r.approval_id})`);
      } else if (r.queued_task_id) {
        parts.push('已入队');
      }
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

async function loadPendingApprovals() {
  try {
    const resp = await fetch('/delivery-approvals?status=pending&limit=200');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data.error) throw new Error(data.error);

    requireConfirmation = !!data.require_confirmation;
    const approvals = data.approvals || [];
    renderApprovalArea(approvals);
  } catch (e) {
    approvalArea.classList.remove('hidden');
    approvalArea.innerHTML = `<div class="approval-header">待确认发送</div><div class="approval-empty">读取待确认列表失败: ${escapeHtml(e.message)}</div>`;
  }
}

function renderApprovalArea(approvals) {
  if (!requireConfirmation) {
    approvalArea.classList.add('hidden');
    approvalArea.innerHTML = '';
    return;
  }

  approvalArea.classList.remove('hidden');
  if (!approvals.length) {
    approvalArea.innerHTML = '<div class="approval-header">待确认发送</div><div class="approval-empty">暂无待确认任务</div>';
    return;
  }

  let html = `<div class="approval-header">待确认发送（${approvals.length}）</div>`;
  html += '<div class="approval-list">';
  approvals.forEach((item) => {
    const contacts = (item.target_contacts || []).join(', ');
    const reviewHtml = formatReviewText(item.review_text || '');
    const imagePaths = item.image_paths || [];

    let imagesHtml = '';
    if (imagePaths.length > 0) {
      imagesHtml = '<div class="approval-images">';
      imagePaths.forEach((p) => {
        const filename = p.replace(/\\/g, '/').split('/').pop();
        const url = '/generated-images/' + encodeURIComponent(filename);
        imagesHtml += `<a href="${url}" target="_blank" class="approval-img-link"><img src="${url}" alt="晒图" class="approval-img" loading="lazy"></a>`;
      });
      imagesHtml += '</div>';
    }

    html += `<div class="approval-card" id="approval-${item.approval_id}">
      <div class="approval-card-header">
        <div>
          <div class="approval-title">${escapeHtml(item.product_title || '未命名商品')}</div>
          <div class="approval-meta">联系人：${escapeHtml(contacts || '-')} ｜ 图片：${imagePaths.length} 张</div>
        </div>
        <div class="approval-actions">
          <button class="btn btn-primary btn-sm" onclick="confirmApproval('${item.approval_id}')">确认并发送</button>
          <button class="btn btn-secondary btn-sm" onclick="rejectApproval('${item.approval_id}')">驳回</button>
        </div>
      </div>
      ${imagesHtml}
      <div class="approval-reviews">${reviewHtml || '<span class="approval-empty-hint">（无评价内容）</span>'}</div>
    </div>`;
  });
  html += '</div>';
  approvalArea.innerHTML = html;
}

function formatReviewText(text) {
  if (!text) return '';
  const blocks = text.split(/\n{2,}/);
  let html = '';
  blocks.forEach((block) => {
    const trimmed = block.trim();
    if (!trimmed) return;
    const isHeading = /^(评价\s*\d|第?\d+[条）\)]|---)/i.test(trimmed);
    const lines = trimmed.split('\n').map(l => escapeHtml(l.trim())).filter(Boolean);
    if (isHeading && lines.length === 1) {
      html += `<div class="review-heading">${lines[0]}</div>`;
    } else {
      html += `<div class="review-block">${lines.join('<br>')}</div>`;
    }
  });
  return html;
}

async function confirmApproval(approvalId) {
  const note = window.prompt('确认发送备注（可选）', '') || '';
  await approveAction(approvalId, 'confirm', note);
}

async function rejectApproval(approvalId) {
  const note = window.prompt('驳回原因（可选）', '') || '';
  await approveAction(approvalId, 'reject', note);
}

async function approveAction(approvalId, action, note) {
  try {
    const btnSelector = `#approval-${approvalId} .approval-actions button`;
    document.querySelectorAll(btnSelector).forEach((b) => { b.disabled = true; });
    const resp = await fetch(`/delivery-approvals/${approvalId}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note }),
    });
    const data = await resp.json();
    if (!resp.ok || data.error) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    await loadPendingApprovals();
  } catch (e) {
    alert(`操作失败: ${e.message}`);
    await loadPendingApprovals();
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
