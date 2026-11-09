const state = {
  data: null,
  selectedAccount: null,
  tab: "courses",
  taskCache: new Map(),
  assessmentCache: new Map(),
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const percent = value => `${Number(value || 0).toFixed(Number(value || 0) % 1 ? 1 : 0)}%`;
const initials = name => [...String(name || "?")].slice(-2).join("");

const statusLabels = {
  running: "执行中", completed: "已完成", failed: "失败", executed: "执行过",
  queued: "等待执行", not_selected: "未选择", idle: "未运行", locked: "待解锁",
  pending: "未执行", resource: "资源", pending_review: "待批阅", incomplete: "未完成",
  expired: "已过期", unknown: "状态未知"
};

function accounts() {
  return (state.data?.schools || []).flatMap(school => school.accounts.map(account => ({...account, school: account.school || school.name})));
}

async function loadDashboard({silent = false} = {}) {
  if (!silent) setSync("loading", "正在同步");
  try {
    const response = await fetch("/api/dashboard", {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    const all = accounts();
    if (!state.selectedAccount || !all.some(a => a.username === state.selectedAccount)) {
      state.selectedAccount = all[0]?.username || null;
    }
    render();
    setSync(state.data.refreshing ? "loading" : "ok", state.data.refreshing ? "平台同步中" : "缓存已更新");
  } catch (error) {
    setSync("error", "连接失败");
    if (!silent) console.error(error);
  }
}

function setSync(kind, label) {
  const node = $(".sync-state");
  node.className = `sync-state ${kind === "ok" ? "" : kind}`;
  $("#syncText").textContent = label;
}

function render() {
  const summary = state.data?.summary || {};
  $("#accountCount").textContent = summary.accounts ?? 0;
  $("#runningCount").textContent = summary.running ?? 0;
  $("#selectedCount").textContent = summary.selected_courses ?? 0;
  $("#updatedAt").textContent = state.data?.updated_at ? new Date(state.data.updated_at).toLocaleString("zh-CN", {hour12:false}) : "等待首次同步";
  renderAccounts();
  renderDetail();
}

function renderAccounts() {
  const term = $("#accountSearch").value.trim().toLowerCase();
  let html = "";
  for (const school of state.data?.schools || []) {
    const rows = school.accounts.filter(a => !term || `${a.name} ${a.username}`.toLowerCase().includes(term));
    if (!rows.length) continue;
    html += `<div class="school-label">${esc(school.name)} · ${rows.length}</div>`;
    html += rows.map(account => `
      <button class="account-button ${account.username === state.selectedAccount ? "active" : ""}" data-account="${esc(account.username)}" type="button">
        <span class="avatar">${esc(initials(account.name))}</span>
        <span class="account-copy"><strong>${esc(account.name)}</strong><span>${esc(account.username)} · ${account.selected_courses || 0} 门</span></span>
        <span class="mini-progress"><strong>${percent(account.progress?.percent)}</strong><i class="${esc(account.status)}"></i></span>
      </button>`).join("");
  }
  $("#accountList").innerHTML = html || `<div class="empty-list">没有匹配账号</div>`;
  $$("[data-account]").forEach(button => button.addEventListener("click", () => {
    state.selectedAccount = button.dataset.account;
    state.tab = "courses";
    render();
  }));
}

function selectedAccount() { return accounts().find(account => account.username === state.selectedAccount); }

function renderDetail() {
  const account = selectedAccount();
  $("#emptyState").hidden = !!account;
  $("#accountDetail").hidden = !account;
  if (!account) return;
  $("#schoolName").textContent = account.school;
  $("#accountNumber").textContent = account.username;
  $("#accountName").textContent = account.name;
  $("#accountSummary").textContent = `${account.selected_courses || 0} 门课在执行列表 · ${account.progress?.done || 0}/${account.progress?.total || 0} 个章节完成`;
  $("#totalPercent").textContent = percent(account.progress?.percent);
  $$(".tab").forEach(tab => {
    const active = tab.dataset.tab === state.tab;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  const courses = state.tab === "courses";
  $("#coursesTab").hidden = !courses;
  $("#assessmentTab").hidden = courses;
  if (courses) renderCourses(account);
  else renderAssessments(account, state.tab);
}

function renderCourses(account) {
  $("#courseList").innerHTML = (account.courses || []).map(course => {
    const p = course.progress;
    const expandable = course.selected && (course.chapters || []).length;
    return `<article class="course-card ${course.selected ? "" : "not-selected"}" data-course-card="${esc(course.key)}">
      <button class="course-main" data-course-toggle="${esc(course.key)}" type="button" ${expandable ? "" : "disabled"}>
        <span class="select-mark ${course.selected ? "" : "off"}">${course.selected ? "✓" : "×"}</span>
        <span class="course-name"><strong>${esc(course.title)}</strong><span>${esc(course.teacher || `班级 ${course.class_id}`)}</span></span>
        <span class="progress-cell"><i class="progress-track"><i class="progress-fill" style="width:${p?.percent || 0}%"></i></i><span>${p ? `${p.done}/${p.total}` : "—"}</span></span>
        <span class="status-pill ${esc(course.status)}">${esc(statusLabels[course.status] || course.status)}</span>
        <span class="chevron">${expandable ? "›" : ""}</span>
      </button>
      <div class="chapter-list">${renderChapters(account, course)}</div>
    </article>`;
  }).join("") || `<div class="empty-list">该账号暂未读取到课程</div>`;

  $$("[data-course-toggle]").forEach(button => button.addEventListener("click", () => {
    button.closest(".course-card")?.classList.toggle("open");
  }));
  $$("[data-chapter-toggle]").forEach(button => button.addEventListener("click", () => toggleChapter(account, button)));
}

function renderChapters(account, course) {
  return (course.chapters || []).map(chapter => `
    <div class="chapter-row" data-chapter-row="${esc(chapter.id)}">
      <button class="chapter-main" data-chapter-toggle="${esc(chapter.id)}" data-course="${esc(course.key)}" type="button">
        <span class="tree-line">└</span><span class="chapter-title">${esc(chapter.title)}</span>
        <span class="chapter-count">${chapter.job_count || 0} 个任务点</span>
        <span class="chapter-state ${esc(chapter.state)}">${esc(statusLabels[chapter.state] || chapter.state)}</span>
      </button>
      <div class="task-list"></div>
    </div>`).join("");
}

async function toggleChapter(account, button) {
  const row = button.closest(".chapter-row");
  row.classList.toggle("open");
  if (!row.classList.contains("open")) return;
  const courseKey = button.dataset.course;
  const chapterId = button.dataset.chapterToggle;
  const key = `${account.username}|${courseKey}|${chapterId}`;
  const target = $(".task-list", row);
  if (state.taskCache.has(key)) { target.innerHTML = renderTasks(state.taskCache.get(key)); return; }
  target.innerHTML = `<div class="loading-line">正在读取任务卡…</div>`;
  try {
    const url = `/api/accounts/${encodeURIComponent(account.username)}/courses/${encodeURIComponent(courseKey)}/chapters/${encodeURIComponent(chapterId)}/tasks`;
    const response = await fetch(url, {cache:"no-store"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.taskCache.set(key, payload.tasks || []);
    target.innerHTML = renderTasks(payload.tasks || []);
  } catch (error) { target.innerHTML = `<div class="error-line">读取失败：${esc(error.message)}</div>`; }
}

function renderTasks(tasks) {
  if (!tasks.length) return `<div class="loading-line">本章节没有可显示的任务卡</div>`;
  return tasks.map(task => `<div class="task-item"><i class="${task.completed ? "completed" : ""}"></i><span>${esc(task.title)}</span><em>${esc(task.completed ? "已完成" : (task.is_task ? "待完成" : "资料"))} · ${esc(task.type)}</em></div>`).join("");
}

async function renderAssessments(account, kind) {
  const key = `${account.username}|${kind}`;
  const target = $("#assessmentContent");
  if (state.assessmentCache.has(key)) { target.innerHTML = assessmentHtml(state.assessmentCache.get(key)); return; }
  target.innerHTML = `<div class="empty-list">正在只读获取${kind === "homework" ? "作业" : "考试"}状态…</div>`;
  try {
    const response = await fetch(`/api/accounts/${encodeURIComponent(account.username)}/assessments?kind=${kind}`, {cache:"no-store"});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.assessmentCache.set(key, payload);
    target.innerHTML = assessmentHtml(payload);
  } catch (error) { target.innerHTML = `<div class="empty-list error-line">读取失败：${esc(error.message)}</div>`; }
}

function assessmentHtml(payload) {
  const s = payload.summary || {};
  let html = `<div class="assessment-summary"><span>合计<strong>${s.total || 0}</strong></span><span>已完成<strong>${s.completed || 0}</strong></span><span>待批阅<strong>${s.pending_review || 0}</strong></span><span>未完成<strong>${s.incomplete || 0}</strong></span></div>`;
  let count = 0;
  for (const course of payload.courses || []) {
    if (!course.items?.length && !course.error) continue;
    count += course.items?.length || 0;
    html += `<section class="assessment-course"><h3>${esc(course.course)}</h3>`;
    if (course.error) html += `<div class="error-line">${esc(course.error)}</div>`;
    else html += course.items.map(item => `<div class="assessment-item"><strong>${esc(item.title)}</strong><span class="assessment-status ${esc(item.state)}">${esc(item.status)}</span><span class="time">${esc(item.deadline)}</span></div>`).join("");
    html += `</section>`;
  }
  if (!count && !(payload.courses || []).some(c => c.error)) html += `<div class="empty-list">选中课程暂无可显示记录</div>`;
  return html;
}

$("#accountSearch").addEventListener("input", renderAccounts);
$("#refreshButton").addEventListener("click", async event => {
  event.currentTarget.disabled = true;
  setSync("loading", "已请求刷新");
  try { await fetch("/api/refresh", {method:"POST"}); }
  finally { setTimeout(() => { event.currentTarget.disabled = false; loadDashboard(); }, 1500); }
});
$$('.tab').forEach(tab => tab.addEventListener("click", () => { state.tab = tab.dataset.tab; renderDetail(); }));

loadDashboard();
setInterval(() => loadDashboard({silent:true}), 15000);
