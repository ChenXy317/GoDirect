let parsedFiles = [];
let currentToken = "";
let currentTab = "all";
let tasksCache = [];
let ws = null;

// 工具函数：HTML 字符串转义以防御 XSS
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// 工具函数：格式化字节大小
function formatBytes(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

// 工具函数：格式化瞬时速度
function formatSpeed(bytesPerSec) {
  if (!bytesPerSec || bytesPerSec <= 0) return "0 KB/s";
  return formatBytes(bytesPerSec) + "/s";
}

// 工具函数：格式化剩余时间
function formatETA(seconds) {
  if (!seconds || seconds <= 0 || !isFinite(seconds)) return "--";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

// 消息提示组件
function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerText = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// 初始化 WebSocket 实时连接
function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    const el = document.getElementById("wsStatus");
    el.innerText = "实时推送已连接";
    el.className = "connection-status connected";
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "metrics" && Array.isArray(data.tasks)) {
        tasksCache = data.tasks;
        renderTasks();
        updateGlobalSpeed();
      }
    } catch (e) {
      console.error("解析 WebSocket 数据异常", e);
    }
  };

  ws.onclose = () => {
    const el = document.getElementById("wsStatus");
    el.innerText = "连接断开，重连中...";
    el.className = "connection-status";
    setTimeout(initWebSocket, 2000);
  };
}

// 更新全局瞬时总下载速度
function updateGlobalSpeed() {
  let totalSpeed = 0;
  for (const t of tasksCache) {
    if (t.status === "downloading" || t.status === "connecting") {
      totalSpeed += t.speed || 0;
    }
  }
  document.getElementById("globalSpeedText").innerText = formatSpeed(totalSpeed);
}

// 解析 Gofile 分享链接
async function handleParse() {
  const urlInput = document.getElementById("inputUrl");
  const pwdInput = document.getElementById("inputPassword");
  const btnParse = document.getElementById("btnParse");

  const url = urlInput.value.trim();
  const password = pwdInput.value.trim();

  if (!url) {
    showToast("请输入有效的 Gofile 链接", "error");
    return;
  }

  btnParse.classList.add("loading");

  try {
    const res = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, password: password || null }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "解析失败");
    }

    if (data.status === "password_required") {
      showToast("此链接受密码保护，请输入密码后再次点击解析", "info");
      document.getElementById("passwordWrapper").scrollIntoView({ behavior: "smooth" });
      pwdInput.focus();
      return;
    }

    parsedFiles = data.items || [];
    currentToken = data.token || "";

    renderParsedFiles();
    showToast(`成功解析出 ${parsedFiles.length} 个文件`, "success");
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btnParse.classList.remove("loading");
  }
}

// 渲染解析结果文件清单
function renderParsedFiles() {
  const resultCard = document.getElementById("resultCard");
  const tbody = document.getElementById("fileListBody");
  const countBadge = document.getElementById("parsedCountBadge");
  const sizeBadge = document.getElementById("parsedSizeBadge");

  tbody.innerHTML = "";

  if (parsedFiles.length === 0) {
    resultCard.style.display = "none";
    return;
  }

  resultCard.style.display = "block";
  countBadge.innerText = `${parsedFiles.length} 个文件`;

  let totalBytes = 0;
  parsedFiles.forEach((file, index) => {
    totalBytes += file.size || 0;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" class="file-chk" data-index="${index}" checked /></td>
      <td><strong>${escapeHtml(file.name)}</strong></td>
      <td style="color: var(--text-muted); font-size: 0.8rem;">${escapeHtml(file.relative_path || file.name)}</td>
      <td>${formatBytes(file.size)}</td>
      <td>
        <button class="btn btn-secondary btn-sm" id="btnDl_${index}" onclick="startSingleDownload(${index})">下载</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  sizeBadge.innerText = formatBytes(totalBytes);
}

// 触发单个文件下载
window.startSingleDownload = async function (index) {
  const file = parsedFiles[index];
  if (!file) return;

  const btn = document.getElementById(`btnDl_${index}`);
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/api/download/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: [{
          name: file.name,
          link: file.link,
          total_size: file.size,
          relative_path: file.relative_path,
        }],
        token: currentToken,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`任务已提交: ${file.name}`);
      if (data.tasks) {
        data.tasks.forEach((t) => {
          const idx = tasksCache.findIndex((x) => x.task_id === t.task_id);
          if (idx >= 0) tasksCache[idx] = t;
          else tasksCache.unshift(t);
        });
        renderTasks();
      }
    }
  } catch (err) {
    showToast("添加下载任务失败", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
};

// 批量提交选中的下载项
async function handleDownloadSelected() {
  const checkboxes = document.querySelectorAll(".file-chk:checked");
  if (checkboxes.length === 0) {
    showToast("未勾选任何文件", "error");
    return;
  }

  const selectedItems = [];
  checkboxes.forEach((chk) => {
    const idx = parseInt(chk.dataset.index);
    const f = parsedFiles[idx];
    if (f) {
      selectedItems.push({
        name: f.name,
        link: f.link,
        total_size: f.size,
        relative_path: f.relative_path,
      });
    }
  });

  try {
    const res = await fetch("/api/download/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: selectedItems, token: currentToken }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`已批量添加 ${selectedItems.length} 个下载任务`);
      if (data.tasks) {
        data.tasks.forEach((t) => {
          const idx = tasksCache.findIndex((x) => x.task_id === t.task_id);
          if (idx >= 0) tasksCache[idx] = t;
          else tasksCache.unshift(t);
        });
        renderTasks();
      }
    }
  } catch (e) {
    showToast("提交批量任务失败", "error");
  }
}

// 导出为带 Cookie 的 cURL 批处理脚本
async function handleExportCurl() {
  const checkboxes = document.querySelectorAll(".file-chk:checked");
  if (checkboxes.length === 0) {
    showToast("请先勾选要导出的文件", "error");
    return;
  }

  const items = [];
  checkboxes.forEach((chk) => {
    const f = parsedFiles[parseInt(chk.dataset.index)];
    if (f) items.push(f);
  });

  try {
    const res = await fetch("/api/export/curl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, token: currentToken }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`cURL 下载脚本已成功导出至:\n${data.bat_path}`, "success");
    }
  } catch (e) {
    showToast("导出批处理脚本失败", "error");
  }
}

// 推送选中项至 IDM 下载队列
async function handlePushIdm() {
  const checkboxes = document.querySelectorAll(".file-chk:checked");
  if (checkboxes.length === 0) {
    showToast("请先勾选要推送至 IDM 的文件", "error");
    return;
  }

  const items = [];
  checkboxes.forEach((chk) => {
    const f = parsedFiles[parseInt(chk.dataset.index)];
    if (f) items.push(f);
  });

  try {
    const res = await fetch("/api/export/idm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, token: currentToken }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`已成功将 ${data.pushed} 个任务推送至 IDM 下载队列`, "success");
    } else {
      showToast(data.detail || "推送至 IDM 失败", "error");
    }
  } catch (e) {
    showToast("请求推送 IDM 失败", "error");
  }
}

// 任务列表平滑渲染
function renderTasks() {
  const container = document.getElementById("tasksList");
  const empty = document.getElementById("emptyTasks");

  const allCount = tasksCache.length;
  const downloadingCount = tasksCache.filter(
    (t) => t.status === "downloading" || t.status === "connecting"
  ).length;
  const completedCount = tasksCache.filter((t) => t.status === "completed").length;

  document.getElementById("countAll").innerText = allCount;
  document.getElementById("countDownloading").innerText = downloadingCount;
  document.getElementById("countCompleted").innerText = completedCount;

  const filtered = tasksCache.filter((t) => {
    if (currentTab === "downloading")
      return t.status === "downloading" || t.status === "connecting" || t.status === "pending";
    if (currentTab === "completed") return t.status === "completed";
    return true;
  });

  if (filtered.length === 0) {
    container.innerHTML = "";
    container.appendChild(empty);
    empty.style.display = "block";
    return;
  }

  if (empty.parentNode === container) {
    empty.remove();
  }

  const existingItems = new Map();
  container.querySelectorAll(".task-item").forEach((el) => {
    existingItems.set(el.dataset.taskId, el);
  });

  const activeIds = new Set();

  filtered.forEach((task) => {
    activeIds.add(task.task_id);
    let item = existingItems.get(task.task_id);

    let statusText = "等待中";
    let statusClass = "status-paused";
    if (task.status === "connecting") {
      statusText = "连接握手中";
      statusClass = "status-downloading";
    } else if (task.status === "downloading") {
      statusText = "高速下载中";
      statusClass = "status-downloading";
    } else if (task.status === "completed") {
      statusText = "已完成";
      statusClass = "status-completed";
    } else if (task.status === "paused") {
      statusText = "已暂停";
      statusClass = "status-paused";
    } else if (task.status === "error") {
      statusText = "下载出错";
      statusClass = "status-error";
    }

    const relText =
      task.relative_path && task.relative_path !== task.name
        ? ` <span style="color: var(--text-muted); font-size: 0.8rem; font-weight: normal;">(${escapeHtml(task.relative_path)})</span>`
        : "";

    const controlsHtml = `
      ${
        task.status === "downloading" || task.status === "connecting"
          ? `<button class="btn btn-secondary btn-sm" onclick="pauseTask('${task.task_id}')">暂停</button>`
          : task.status === "paused" || task.status === "error"
          ? `<button class="btn btn-primary btn-sm" onclick="resumeTask('${task.task_id}')">继续</button>`
          : ""
      }
      <button class="btn btn-secondary btn-sm" onclick="cancelTask('${task.task_id}')">删除</button>
    `;

    const metricsHtml = `
      <span>进度: ${task.percent}% (${formatBytes(task.downloaded_size)} / ${formatBytes(task.total_size)})</span>
      ${task.status === "downloading" ? `<span>速度: ${formatSpeed(task.speed)}</span>` : ""}
      ${task.status === "downloading" && task.eta > 0 ? `<span>剩余时间: ${formatETA(task.eta)}</span>` : ""}
      ${task.error_message ? `<span style="color: var(--accent-red);">${escapeHtml(task.error_message)}</span>` : ""}
    `;

    if (!item) {
      item = document.createElement("div");
      item.className = "task-item";
      item.dataset.taskId = task.task_id;
      item.innerHTML = `
        <div class="task-top">
          <div class="task-title">
            <span>${escapeHtml(task.name)}</span>
            ${relText}
          </div>
          <span class="task-status-tag ${statusClass}">${statusText}</span>
        </div>
        <div class="progress-track">
          <div class="progress-bar" style="width: ${task.percent}%"></div>
        </div>
        <div class="task-bottom">
          <div class="task-metrics">${metricsHtml}</div>
          <div class="task-controls">${controlsHtml}</div>
        </div>
      `;
      container.appendChild(item);
    } else {
      const tag = item.querySelector(".task-status-tag");
      if (tag) {
        tag.className = `task-status-tag ${statusClass}`;
        tag.innerText = statusText;
      }
      const bar = item.querySelector(".progress-bar");
      if (bar) {
        bar.style.width = `${task.percent}%`;
      }
      const metrics = item.querySelector(".task-metrics");
      if (metrics) {
        metrics.innerHTML = metricsHtml;
      }
      const controls = item.querySelector(".task-controls");
      if (controls) {
        controls.innerHTML = controlsHtml;
      }
    }
  });

  existingItems.forEach((el, id) => {
    if (!activeIds.has(id)) {
      el.remove();
    }
  });
}

// 任务控制函数绑定
window.pauseTask = async (taskId) => {
  await fetch("/api/download/pause", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId }),
  });
};

window.resumeTask = async (taskId) => {
  await fetch("/api/download/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId }),
  });
};

window.cancelTask = async (taskId) => {
  await fetch("/api/download/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId }),
  });
  tasksCache = tasksCache.filter((t) => t.task_id !== taskId);
  renderTasks();
};

// 设置面板逻辑
async function openSettings() {
  const modal = document.getElementById("settingsModal");
  modal.style.display = "flex";
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();
    if (data.config) {
      document.getElementById("settingDownloadDir").value = data.config.download_dir || "";
      document.getElementById("settingProxy").value = data.config.proxy || "";
      document.getElementById("settingToken").value = data.config.account_token || "";
      document.getElementById("settingMaxTasks").value = data.config.max_concurrent_tasks || 3;
      const chunkInput = document.getElementById("settingChunkThreads");
      if (chunkInput) {
        chunkInput.value = data.config.chunk_threads || 4;
      }
    }
  } catch (e) {
    showToast("读取配置失败", "error");
  }
}

async function saveSettings() {
  const download_dir = document.getElementById("settingDownloadDir").value.trim();
  const proxy = document.getElementById("settingProxy").value.trim();
  const account_token = document.getElementById("settingToken").value.trim();
  const max_concurrent_tasks = parseInt(document.getElementById("settingMaxTasks").value) || 3;
  const chunkInput = document.getElementById("settingChunkThreads");
  const chunk_threads = chunkInput ? parseInt(chunkInput.value) || 4 : 4;

  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        download_dir,
        proxy,
        account_token,
        max_concurrent_tasks,
        chunk_threads,
      }),
    });
    if (res.ok) {
      showToast("配置保存成功", "success");
      document.getElementById("settingsModal").style.display = "none";
    }
  } catch (e) {
    showToast("保存配置异常", "error");
  }
}

// 事件监听器挂载
document.addEventListener("DOMContentLoaded", () => {
  initWebSocket();

  document.getElementById("btnParse").addEventListener("click", handleParse);
  document.getElementById("inputUrl").addEventListener("keydown", (e) => {
    if (e.key === "Enter") handleParse();
  });

  document.getElementById("btnSelectAll").addEventListener("click", () => {
    const chks = document.querySelectorAll(".file-chk");
    const anyUnchecked = Array.from(chks).some((c) => !c.checked);
    chks.forEach((c) => (c.checked = anyUnchecked));
    document.getElementById("chkHeaderSelectAll").checked = anyUnchecked;
  });

  document.getElementById("chkHeaderSelectAll").addEventListener("change", (e) => {
    document.querySelectorAll(".file-chk").forEach((c) => (c.checked = e.target.checked));
  });

  document.getElementById("btnDownloadSelected").addEventListener("click", handleDownloadSelected);
  document.getElementById("btnExportCurl").addEventListener("click", handleExportCurl);
  document.getElementById("btnPushIdm").addEventListener("click", handlePushIdm);

  // 任务选项卡切换
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentTab = btn.dataset.tab;
      renderTasks();
    });
  });

  // 打开保存目录
  document.getElementById("btnOpenFolder").addEventListener("click", async () => {
    await fetch("/api/download/open-folder", { method: "POST" });
  });

  // 设置面板
  document.getElementById("btnOpenSettings").addEventListener("click", openSettings);
  document.getElementById("btnCloseSettings").addEventListener("click", () => {
    document.getElementById("settingsModal").style.display = "none";
  });
  document.getElementById("btnCancelSettings").addEventListener("click", () => {
    document.getElementById("settingsModal").style.display = "none";
  });
  document.getElementById("btnSaveSettings").addEventListener("click", saveSettings);
});
