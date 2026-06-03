const state = {
  currentAccount: null,
  authRequired: true,
  projects: [],
  files: [],
  selectedProjectId: null,
  workflowSummary: null,
  modelConfig: null,
  excellentBidLibrary: null,
  selectedLibrarySourceId: null,
  activeStep: "upload",
  artifactPreviewCache: {},
  outlineCollapsed: new Set(),
  selectedGenerationUnits: new Set(),
  activeView: "home",
  pollingTimer: null,
  pollingInFlight: false,
  activeStepPinned: false,
  knownJobStatuses: new Map(),
  assistantReportedJobs: new Set(),
  workflowRefreshSeq: 0,
  aiRefreshSeq: 0,
  currentWorkflowPollMs: 1500,
  parseConfirmations: {},
  aiSummary: null,
  bidTemplates: null,
  bidTemplateRecommendation: null,
  selectedBidTemplateId: null,
  bidTemplateFilters: { query: "", project_type: "", tag: "" },
  ragMaterials: null,
  ragMaterialsQueryKey: "",
  assistantLastSyncedAt: null,
  assistantSyncing: false,
  wordSummary: null,
  wordProfile: null,
  onlyOfficeConfig: null,
  onlyOfficeEditor: null,
  accounts: null,
  assistantAnswerPending: false,
  assistantChatSeq: 0,
  sidebarCollapsed: safeStorageGet("zb-sidebar-collapsed") === "1",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

document.addEventListener("click", handleShellChromeClick, true);

function safeStorageGet(key) {
  try {
    return globalThis.localStorage?.getItem(key) || "";
  } catch {
    return "";
  }
}

function safeStorageSet(key, value) {
  try {
    globalThis.localStorage?.setItem(key, value);
  } catch {
    return;
  }
}

function handleShellChromeClick(event) {
  const target = event.target && typeof event.target.closest === "function" ? event.target : null;
  if (!target) return;
  const sidebarToggle = target.closest("#sidebar-collapse-toggle");
  if (sidebarToggle) {
    event.preventDefault();
    event.stopPropagation();
    toggleSidebarCollapsed();
    return;
  }
  const navItem = target.closest(".nav-item[data-view]");
  if (navItem) {
    event.preventDefault();
    event.stopPropagation();
    switchView(navItem.dataset.view || "home");
    return;
  }
  const viewButton = target.closest("[data-open-view]");
  if (viewButton) {
    event.preventDefault();
    event.stopPropagation();
    switchView(viewButton.dataset.openView || "home");
    return;
  }
  const assistantButton = target.closest("[data-open-assistant]");
  if (assistantButton) {
    event.preventDefault();
    event.stopPropagation();
    toggleAssistantDock(true);
    return;
  }
  const placeholderRefresh = target.closest("[data-placeholder-refresh]");
  if (placeholderRefresh) {
    event.preventDefault();
    event.stopPropagation();
    refreshHealth();
    toast("平台状态已刷新");
  }
}

function unwrap(response) {
  if (!response.success) {
    throw new Error(response.error?.message || "请求失败");
  }
  return response.data;
}

function userFacingErrorMessage(error) {
  return String(error?.message || "操作失败").replace(/（请求\s*[^）]+）/g, "");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) {
    const requestId = payload.request_id ? `（请求 ${payload.request_id}）` : "";
    const message = payload.error?.message || payload.detail || response.statusText;
    if (response.status === 401 && !options.skipAuthRedirect) {
      showAuthScreen(message);
    }
    throw new Error(`${message}${requestId}`);
  }
  return unwrap(payload);
}

function isAdmin() {
  return state.currentAccount?.role === "admin";
}

function showAuthScreen(message = "") {
  $("#auth-screen")?.classList.remove("hidden");
  document.body.classList.add("auth-required");
  if (message) {
    const el = $("#auth-message");
    if (el) el.textContent = message;
  }
}

function hideAuthScreen() {
  $("#auth-screen")?.classList.add("hidden");
  document.body.classList.remove("auth-required");
}

function applyAuthUi() {
  $$(".admin-only").forEach((element) => {
    element.classList.toggle("hidden", !isAdmin());
  });
  const chip = $("#logout-button");
  if (chip) {
    chip.textContent = state.currentAccount ? `${state.currentAccount.display_name || state.currentAccount.username} · 退出` : "退出";
  }
  if (!isAdmin() && state.activeView === "accounts") {
    switchView("home");
  }
}

function switchView(view) {
  const targetView = view || "projects";
  if (targetView === "accounts" && !isAdmin()) {
    toast("仅系统管理员可进入账户管理");
    return;
  }
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === targetView));
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${targetView}`));
  state.activeView = targetView;
  if (targetView === "model") {
    refreshModelConfig();
  }
  if (targetView === "rag") {
    refreshExcellentBidLibrary();
  }
  if (targetView === "templates") {
    refreshBidTemplates();
  }
  if (targetView === "accounts") {
    refreshAccounts();
  }
  renderTopProjectContext();
  renderAssistantPageAdvice();
  renderAssistantQuickActions();
  renderAiAssistantPanel();
  if (state.selectedProjectId) {
    refreshAiContext({ silent: true });
  } else {
    renderAiAssistantEmpty();
  }
}

async function refreshCurrentAccount() {
  const payload = await api("/api/v1/auth/me", { skipAuthRedirect: true });
  state.authRequired = payload.auth_required !== false;
  state.currentAccount = payload.account || null;
  if (payload.authenticated || !state.authRequired) {
    hideAuthScreen();
    applyAuthUi();
    return true;
  }
  showAuthScreen();
  applyAuthUi();
  return false;
}

function toast(message) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  window.setTimeout(() => el.classList.remove("show"), 2800);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmtTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function fmtProjectType(value) {
  if (value === "construction") return "施工总承包";
  if (value === "epc") return "EPC";
  return "自动识别";
}

function fmtBusinessType(value) {
  const map = {
    tender_document: "招标文件",
    excellent_bid: "参考资料",
    other: "其他资料",
  };
  return map[value] || value || "-";
}

function fmtKnowledgeType(value) {
  const map = {
    excellent_bid: "优秀标书",
    law_regulation: "法律法规",
    technical_standard: "技术规范",
    enterprise_policy: "企业制度",
    review_rule: "评审办法",
    other: "其他资料",
  };
  return map[value] || value || "参考资料";
}

const RAG_KNOWLEDGE_TYPE_ORDER = [
  "law_regulation",
  "technical_standard",
  "enterprise_policy",
  "review_rule",
  "excellent_bid",
  "other",
];

function fmtStage(value) {
  const map = {
    draft: "草稿",
    parsing: "解析中",
    parsed: "已解析",
    outlining: "目录生成中",
    outlined: "目录已生成",
    generating: "正文生成中",
    generated: "正文已生成",
    reviewing: "复核中",
    completed: "已完成",
  };
  return map[value] || value || "草稿";
}

function fmtProfileName(key) {
  const map = {
    project_info_extraction_input: "项目基础信息抽取输入包",
    score_points_extraction_input: "技术标评分点抽取输入包",
    technical_requirements_extraction_input: "技术标准与要求抽取输入包",
    outline_refinement: "二三级目录补强",
    technical_bid_chapter_generation: "技术标章节正文生成",
  };
  return map[key] || key;
}

function fmtProfileLine(profile) {
  const maxTokens = profile.max_tokens ?? "-";
  const timeout = profile.timeout_seconds ?? "-";
  const workers = profile.max_workers ?? "-";
  const temperature = profile.temperature ?? "-";
  return `最大输出：${maxTokens} · 超时：${timeout} 秒 · 并发：${workers} · 温度：${temperature}`;
}

function profileFieldValue(profile, key) {
  const value = profile?.[key];
  return value === null || value === undefined ? "" : value;
}

function profileChecked(profile, key) {
  return Boolean(profile?.[key]);
}

function fmtNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function fmtDurationSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 60) return `${seconds.toFixed(seconds >= 10 ? 0 : 1)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remain = Math.round(seconds % 60);
  return `${minutes} 分 ${remain} 秒`;
}

function fmtSyncTime(value) {
  if (!value) return "尚未同步";
  return new Date(value).toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function fmtJobStatus(status) {
  const map = {
    pending: "排队中",
    running: "执行中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return map[status] || status || "未知状态";
}

function fmtWordVersionName(key) {
  const map = {
    system_generated: "系统生成稿",
    review_editing: "在线复核稿",
    final_export: "最终导出稿",
  };
  return map[key] || key || "-";
}

function fmtWordStatus(value) {
  const map = {
    ready: "已生成",
    missing: "未生成",
  };
  return map[value] || value || "未生成";
}

function setNestedValue(target, path, value) {
  const parts = String(path || "").split(".").filter(Boolean);
  let cursor = target;
  parts.forEach((part, index) => {
    if (index === parts.length - 1) {
      cursor[part] = value;
      return;
    }
    if (!cursor[part] || typeof cursor[part] !== "object") {
      cursor[part] = {};
    }
    cursor = cursor[part];
  });
}

function getNestedValue(target, path) {
  return String(path || "")
    .split(".")
    .filter(Boolean)
    .reduce((cursor, part) => (cursor && typeof cursor === "object" ? cursor[part] : undefined), target);
}

function currentStepSummary() {
  const steps = state.workflowSummary?.steps || [];
  return steps.find((step) => step.key === state.activeStep) || null;
}

function recommendedActiveStep(summary = state.workflowSummary) {
  const steps = summary?.steps || [];
  const active = steps.find((step) => step.status === "active");
  if (active?.key) return active.key;
  const lastDone = [...steps].reverse().find((step) => step.status === "done");
  if (!lastDone?.key) return "upload";
  if (lastDone.key === "upload") return "parse";
  if (lastDone.key === "parse") return "outline";
  if (lastDone.key === "outline") return "generate";
  if (lastDone.key === "generate") return "review";
  return lastDone.key || "upload";
}

const WORKFLOW_STEP_ORDER = ["upload", "parse", "outline", "generate", "review"];

function workflowStepRank(step) {
  const rank = WORKFLOW_STEP_ORDER.indexOf(step);
  return rank >= 0 ? rank : 0;
}

function syncActiveStepWithWorkflow(summary = state.workflowSummary, options = {}) {
  if (!summary) return;
  const nextStep = recommendedActiveStep(summary);
  if (!nextStep || nextStep === state.activeStep) return;
  if ((summary.latest_jobs || []).some(isActiveJob)) {
    setActiveStep(nextStep, { userInitiated: false, refreshStepData: false });
    return;
  }
  if (state.activeStepPinned && options.force !== true) {
    const currentRank = workflowStepRank(state.activeStep);
    const nextRank = workflowStepRank(nextStep);
    if (nextRank <= currentRank) {
      return;
    }
  }
  if (nextStep) {
    setActiveStep(nextStep, { userInitiated: false, refreshStepData: false });
  }
}

function activeNavLabel() {
  const map = {
    home: "平台首页",
    qa: "智能问答",
    "bid-intel": "标讯情报",
    risk: "企业风险",
    suppliers: "供应商推荐",
    projects: "技术标编制",
    rag: "投标知识库",
    templates: "投标模板",
    model: "系统设置",
    accounts: "账户管理",
  };
  return map[state.activeView] || "工作台";
}

function applySidebarState() {
  document.body.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
  const toggle = $("#sidebar-collapse-toggle");
  if (toggle) {
    toggle.setAttribute("aria-expanded", state.sidebarCollapsed ? "false" : "true");
    toggle.setAttribute("aria-label", state.sidebarCollapsed ? "展开侧边栏" : "收起侧边栏");
    toggle.title = state.sidebarCollapsed ? "展开侧边栏" : "收起侧边栏";
  }
}

function toggleSidebarCollapsed(force = null) {
  state.sidebarCollapsed = force === null ? !state.sidebarCollapsed : Boolean(force);
  safeStorageSet("zb-sidebar-collapsed", state.sidebarCollapsed ? "1" : "0");
  applySidebarState();
}

function isActiveJob(job) {
  if (typeof job?.is_active === "boolean") return job.is_active;
  return ["pending", "running"].includes(job?.effective_status || job?.status);
}

function jobStatus(job) {
  return job?.effective_status || job?.status || "";
}

function scorePointKey(item, index) {
  return `${index + 1}:${item.title || ""}`;
}

function projectConfirmationState(projectId = state.selectedProjectId) {
  if (!projectId) return {};
  if (!state.parseConfirmations[projectId]) {
    try {
      state.parseConfirmations[projectId] = JSON.parse(window.localStorage.getItem(`parse_confirmations:${projectId}`) || "{}");
    } catch (_error) {
      state.parseConfirmations[projectId] = {};
    }
  }
  return state.parseConfirmations[projectId];
}

function saveProjectConfirmationState(projectId = state.selectedProjectId) {
  if (!projectId) return;
  window.localStorage.setItem(`parse_confirmations:${projectId}`, JSON.stringify(projectConfirmationState(projectId)));
}

function isScorePointConfirmed(item, index) {
  return Boolean(projectConfirmationState()[scorePointKey(item, index)]);
}

function setScorePointConfirmed(index, confirmed = true) {
  const items = state.workflowSummary?.score_points || [];
  const item = items[index];
  if (!item) return;
  const confirmations = projectConfirmationState();
  const key = scorePointKey(item, index);
  if (confirmed) {
    confirmations[key] = { confirmed_at: new Date().toISOString() };
  } else {
    delete confirmations[key];
  }
  saveProjectConfirmationState();
  renderScorePoints(items);
  renderParseReviewPanel();
  renderAssistantReviewSummary();
  renderAssistantScoreBoard();
}

function confirmAllScorePoints() {
  const items = state.workflowSummary?.score_points || [];
  if (!items.length) {
    toast("暂无评分点可确认");
    return;
  }
  const confirmations = projectConfirmationState();
  const confirmedAt = new Date().toISOString();
  items.forEach((item, index) => {
    confirmations[scorePointKey(item, index)] = { confirmed_at: confirmedAt };
  });
  saveProjectConfirmationState();
  renderScorePoints(items);
  renderParseReviewPanel();
  renderAssistantReviewSummary();
  renderAssistantScoreBoard();
  toast("评分点已全部确认");
}

function toggleCreateProjectForm() {
  $("#project-form")?.classList.toggle("expanded");
}

function artifactUrl(projectId, key) {
  return `/api/v1/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(key)}`;
}

function artifactDownloadUrl(projectId, key) {
  return `${artifactUrl(projectId, key)}/download`;
}

function simpleMarkdownToHtml(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const html = [];
  let listOpen = false;
  let tableRows = [];

  const closeList = () => {
    if (listOpen) {
      html.push("</ul>");
      listOpen = false;
    }
  };
  const flushTable = () => {
    if (!tableRows.length) return;
    const rows = tableRows
      .filter((row) => !/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(row))
      .map((row, index) => {
        const cells = row
          .replace(/^\s*\|/, "")
          .replace(/\|\s*$/, "")
          .split("|")
          .map((cell) => `<${index === 0 ? "th" : "td"}>${escapeHtml(cell.trim())}</${index === 0 ? "th" : "td"}>`)
          .join("");
        return `<tr>${cells}</tr>`;
      })
      .join("");
    if (rows) html.push(`<table>${rows}</table>`);
    tableRows = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (/^\s*\|.+\|\s*$/.test(line)) {
      closeList();
      tableRows.push(line);
      continue;
    }
    flushTable();
    if (!line.trim()) {
      closeList();
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 1, 5);
      html.push(`<h${level}>${escapeHtml(heading[2])}</h${level}>`);
      continue;
    }
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      if (!listOpen) {
        html.push("<ul>");
        listOpen = true;
      }
      html.push(`<li>${escapeHtml(bullet[1])}</li>`);
      continue;
    }
    closeList();
    html.push(`<p>${escapeHtml(line)}</p>`);
  }
  closeList();
  flushTable();
  return html.join("");
}

function currentProject() {
  const fromList = state.projects.find((item) => item.project_id === state.selectedProjectId);
  return fromList || state.workflowSummary?.project || null;
}

function renderTopProjectContext() {
  const project = currentProject();
  const stepLabel = state.activeView === "projects" ? currentStepSummary()?.title || fmtStepName(state.activeStep) : activeNavLabel();
  $("#top-project-name").textContent = project?.name || "未选择项目";
  $("#top-project-meta").textContent = project
    ? `${fmtProjectType(project.project_type)} · ${project.stage_label || fmtStage(project.stage)} · ${stepLabel}`
    : "选择项目后展示类型与阶段";
}

async function refreshHealth() {
  try {
    const data = await api("/api/v1/health");
    const health = runtimeHealthView(data);
    const healthDot = $("#health-dot");
    const healthText = $("#health-text");
    const topHealthDot = $("#top-health-dot");
    const topHealthText = $("#top-health-text");
    if (healthDot) healthDot.className = `dot ${health.level}`;
    if (healthText) healthText.textContent = health.sidebarText;
    if (topHealthDot) topHealthDot.className = health.level;
    if (topHealthText) {
      topHealthText.textContent = health.toolbarText;
      topHealthText.title = health.tooltip;
    }
  } catch (error) {
    const healthDot = $("#health-dot");
    const healthText = $("#health-text");
    const topHealthDot = $("#top-health-dot");
    const topHealthText = $("#top-health-text");
    if (healthDot) healthDot.className = "dot bad";
    if (healthText) healthText.textContent = "后台异常";
    if (topHealthDot) topHealthDot.className = "bad";
    if (topHealthText) {
      topHealthText.textContent = "后台异常";
      topHealthText.title = userFacingErrorMessage(error);
    }
  }
}

function runtimeHealthView(data = {}) {
  const mode = data.runtime_storage || (data.database === "ok" ? "postgres" : "dev_json");
  if (mode === "postgres") {
    return {
      level: "ok",
      sidebarText: "运行正常",
      toolbarText: "运行正常",
      tooltip: "后台服务运行正常。",
    };
  }
  if (mode === "dev_json") {
    return {
      level: "warn",
      sidebarText: "演示模式",
      toolbarText: "演示模式",
      tooltip: "当前使用本地演示数据，适合试用和演示。",
    };
  }
  return {
    level: "bad",
    sidebarText: "后台异常",
    toolbarText: "后台异常",
    tooltip: "后台服务暂不可用，请联系管理员。",
  };
}

async function refreshProjects(options = {}) {
  const autoSelect = options.autoSelect === true;
  const refreshWorkspaceOption = options.refreshWorkspace !== false;
  try {
    const projects = await api("/api/v1/projects");
    state.projects = projects;
    const selectedStillExists = state.selectedProjectId && projects.some((item) => item.project_id === state.selectedProjectId);
    if (!selectedStillExists) {
      state.selectedProjectId = autoSelect && projects.length ? projects[0].project_id : null;
    }
    renderProjects();
    if (refreshWorkspaceOption) {
      await refreshWorkspace();
    } else {
      renderTopProjectContext();
    }
  } catch (error) {
    toast(`项目加载失败：${userFacingErrorMessage(error)}`);
  }
}

function renderProjects() {
  const projectCount = $("#project-count");
  if (projectCount) {
    projectCount.textContent = `${state.projects.length} 个项目`;
  }
  const select = $("#project-select");
  select.innerHTML = "";

  if (!state.projects.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "暂无项目，请先新建";
    select.appendChild(option);
    renderProjectSelectorMeta(null);
    return;
  }

  if (!state.selectedProjectId) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "请选择项目";
    option.selected = true;
    select.appendChild(option);
  }

  for (const project of state.projects) {
    const option = document.createElement("option");
    option.value = project.project_id;
    option.textContent = project.name;
    option.selected = project.project_id === state.selectedProjectId;
    select.appendChild(option);
  }
  renderProjectSelectorMeta(currentProject());
}

async function selectProject(projectId) {
  state.selectedProjectId = projectId || null;
  state.activeStep = "upload";
  state.activeStepPinned = false;
  state.artifactPreviewCache = {};
  state.wordSummary = null;
  state.wordProfile = null;
  state.onlyOfficeConfig = null;
  state.aiSummary = null;
  state.bidTemplateRecommendation = null;
  state.ragMaterials = null;
  state.ragMaterialsQueryKey = "";
  renderProjects();
  setActiveStep(state.activeStep, { userInitiated: false, refreshStepData: false });
  await refreshWorkspace();
}

async function refreshWorkspace() {
  if (!state.selectedProjectId) {
    state.files = [];
    state.workflowSummary = null;
    state.aiSummary = null;
    state.bidTemplateRecommendation = null;
    state.ragMaterials = null;
    state.ragMaterialsQueryKey = "";
    state.assistantLastSyncedAt = null;
    state.assistantSyncing = false;
    renderTopProjectContext();
    updateWorkflowPolling([]);
    $("#empty-workspace").classList.remove("hidden");
    $("#project-workspace").classList.add("hidden");
    renderAiAssistantEmpty();
    renderAiInsightPanel();
    return;
  }

  $("#empty-workspace").classList.add("hidden");
  $("#project-workspace").classList.remove("hidden");
  renderProjectHeader();
  await refreshProjectContext({ silent: true, refreshProjectsList: false });
}

async function refreshProjectData() {
  await refreshProjects({ refreshWorkspace: false });
  await refreshProjectContext({ silent: true });
}

function renderProjectHeader() {
  const project = currentProject();
  if (!project) return;
  $("#active-project-name").textContent = project.name;
  $("#active-project-meta").textContent = `${fmtProjectType(project.project_type)} · ${project.stage_label || fmtStage(project.stage)} · 更新于 ${fmtTime(project.updated_at)}`;
  $("#file-scope").textContent = project.name;
  renderTopProjectContext();
  renderProjectSelectorMeta(project);
}

function renderProjectSelectorMeta(project) {
  $("#selected-project-type").textContent = project ? fmtProjectType(project.project_type) : "未选择";
  $("#selected-project-stage").textContent = project ? project.stage_label || fmtStage(project.stage) : "未选择";
}

async function refreshFiles(projectId) {
  try {
    state.files = await api(`/api/v1/projects/${projectId}/files`);
    renderFiles();
  } catch (error) {
    toast(`文件加载失败：${error.message}`);
  }
}

function renderFiles() {
  const list = $("#files-list");
  list.className = state.files.length ? "list" : "list empty";
  list.innerHTML = state.files.length ? "" : "暂无上传文件。";

  for (const file of state.files) {
    const item = document.createElement("div");
    item.className = "file-item";
    item.innerHTML = `
      <div class="file-main">
        <strong>${escapeHtml(file.file_name)}</strong>
        <span class="muted">${escapeHtml(fmtBusinessType(file.business_type))} · ${escapeHtml(file.file_ext || "-")} · ${Number(file.file_size || 0).toLocaleString("zh-CN")} bytes · ${escapeHtml(file.status)}</span>
      </div>
      <button type="button" class="danger-button small-button" data-delete-file="${escapeHtml(file.file_id)}">删除</button>
    `;
    list.appendChild(item);
  }
  list.querySelectorAll("[data-delete-file]").forEach((button) => {
    button.addEventListener("click", () => deleteUploadedFile(button.dataset.deleteFile));
  });
}

async function refreshWorkflowSummary(projectId) {
  const seq = ++state.workflowRefreshSeq;
  try {
    const summary = await api(`/api/v1/projects/${projectId}/workflow-summary`);
    if (seq !== state.workflowRefreshSeq || projectId !== state.selectedProjectId) return;
    state.workflowSummary = summary;
    syncActiveStepWithWorkflow(state.workflowSummary, { force: (summary.latest_jobs || []).some(isActiveJob) });
    renderProjectHeader();
    renderWorkflowSummary();
  } catch (error) {
    toast(`工作流摘要加载失败：${error.message}`);
  }
}

async function refreshProjectSnapshot(options = {}) {
  await refreshProjectContext(options);
}

async function refreshProjectContext(options = {}) {
  if (!state.selectedProjectId) {
    await refreshWorkspace();
    return;
  }
  const projectId = state.selectedProjectId;
  const silent = options.silent !== false;
  if (options.refreshProjectsList) {
    await refreshProjects({ refreshWorkspace: false });
    if (!state.selectedProjectId || state.selectedProjectId !== projectId) {
      await refreshWorkspace();
      return;
    }
  }
  renderProjectHeader();
  const tasks = [];
  if (options.refreshFiles !== false) {
    tasks.push(refreshFiles(projectId));
  }
  if (options.refreshWorkflow !== false) {
    tasks.push(refreshWorkflowSummary(projectId));
  }
  await Promise.all(tasks);
  if (projectId !== state.selectedProjectId) return;
  renderProjectHeader();
  if (options.refreshAssistant !== false) {
    await refreshAiContext({ silent });
  } else {
    renderAiAssistantPanel();
    renderAiInsightPanel();
  }
}

function assistantRagQueryKey(projectId = state.selectedProjectId) {
  const chapter = selectedGenerationUnitTitle();
  return [projectId || "", chapter || "", state.activeStep || "", state.activeView || ""].join("|");
}

async function refreshAiContext(options = {}) {
  if (!state.selectedProjectId) {
    state.assistantLastSyncedAt = null;
    state.assistantSyncing = false;
    renderAiAssistantEmpty();
    return;
  }
  const nextRagQueryKey = assistantRagQueryKey(state.selectedProjectId);
  state.assistantSyncing = true;
  state.aiSummary = null;
  if (state.ragMaterialsQueryKey !== nextRagQueryKey) {
    state.ragMaterials = null;
    state.ragMaterialsQueryKey = "";
  }
  renderAiAssistantPanel();
  renderAiInsightPanel();
  try {
    await refreshAiAssistantPanel({ silent: options.silent !== false, ragQueryKey: nextRagQueryKey });
  } finally {
    state.assistantSyncing = false;
    renderAiAssistantPanel();
  }
}

function renderWorkflowSummary() {
  const summary = state.workflowSummary;
  if (!summary) return;

  $("#metric-files").textContent = summary.stats?.files ?? 0;
  $("#metric-score").textContent = summary.stats?.score_points ?? 0;
  $("#metric-review").textContent = summary.stats?.review_items ?? 0;
  renderWorkflowSteps(summary.steps || []);
  renderWorkflowTiming(summary.generation_report?.metrics?.stage_timings || []);
  renderStageBanner(summary.latest_jobs || []);
  updateWorkflowActionButtons(summary);
  renderScorePoints(summary.score_points || []);
  renderParseReviewPanel();
  renderOutline(summary.outline_preview || []);
  renderGenerationUnits(summary.generation_units || []);
  renderGenerationConcurrencyHint(state.modelConfig?.tasks?.technical_bid_chapter_generation || {});
  renderReviewItems(summary.review_items || []);
  renderArtifacts(summary.artifacts || {});
  renderArtifactDownloadButtons(summary.artifacts || {});
  renderWordExportPage();
  renderLatestJobs(summary.latest_jobs || []);
  updateWorkflowPolling(summary.latest_jobs || []);
  renderAiInsightPanel();
}

function workflowStepDone(summary, key) {
  return (summary?.steps || []).some((step) => step.key === key && step.status === "done");
}

async function refreshAiAssistantPanel(options = {}) {
  if (!state.selectedProjectId) {
    state.ragMaterials = null;
    state.ragMaterialsQueryKey = "";
    state.assistantLastSyncedAt = null;
    state.assistantSyncing = false;
    renderAiAssistantEmpty();
    return;
  }
  const projectId = state.selectedProjectId;
  const seq = ++state.aiRefreshSeq;
  try {
    const chapter = selectedGenerationUnitTitle();
    const ragQueryKey = options.ragQueryKey || assistantRagQueryKey(projectId);
    const ragMaterialsPromise =
      state.ragMaterials && state.ragMaterialsQueryKey === ragQueryKey
        ? Promise.resolve(state.ragMaterials)
        : api(
            `/api/v1/projects/${projectId}/rag/materials?${new URLSearchParams({
              chapter,
              limit: "3",
            }).toString()}`
          );
    const templatePromise = state.bidTemplateRecommendation
      ? Promise.resolve(state.bidTemplateRecommendation)
      : api(`/api/v1/projects/${projectId}/bid-template/recommendation`);
    const [aiSummary, templateRecommendation, ragMaterials] = await Promise.all([
      api(`/api/v1/projects/${projectId}/assistant/summary`),
      templatePromise,
      ragMaterialsPromise,
    ]);
    if (seq !== state.aiRefreshSeq || projectId !== state.selectedProjectId) return;
    state.aiSummary = aiSummary;
    state.bidTemplateRecommendation = templateRecommendation;
    state.ragMaterials = ragMaterials;
    state.ragMaterialsQueryKey = ragQueryKey;
    state.assistantLastSyncedAt = new Date().toISOString();
    renderAiAssistantPanel();
    renderAiInsightPanel();
  } catch (error) {
    if (!options.silent) {
      toast(`AI 助手刷新失败：${userFacingErrorMessage(error)}`);
    }
  }
}

function renderAiAssistantEmpty() {
  $("#assistant-context-label").textContent = "请先选择项目";
  $("#ai-summary-state").textContent = "暂无项目上下文。";
  $("#ai-summary-text").textContent = "请先在“编标项目”中新建或选择项目，再询问项目进度、下一步建议、模板和智库依据。";
  const syncLabel = $("#assistant-sync-label");
  if (syncLabel) syncLabel.textContent = "未连接项目";
  renderAssistantReviewSummary();
  renderAssistantScoreBoard();
  renderAssistantPageAdvice();
  $("#ai-summary-risks").innerHTML = "";
  renderAssistantGenerationSummary();
  renderAssistantTaskSummary();
  renderAssistantRagEvidence();
}

function renderAiAssistantPanel() {
  const summary = state.aiSummary;
  const syncLabel = $("#assistant-sync-label");
  if (syncLabel) {
    syncLabel.textContent = state.assistantSyncing
      ? "同步中..."
      : `同步 ${fmtSyncTime(state.assistantLastSyncedAt)}`;
  }
  if (!summary) {
    const stepLabel = currentStepSummary()?.title || fmtStepName(state.activeStep);
    $("#assistant-context-label").textContent = `${activeNavLabel()} · ${currentProject()?.name || "当前项目"} · ${stepLabel}`;
    $("#ai-summary-state").textContent = "正在读取项目状态";
    $("#ai-summary-text").textContent = "助手正在同步最新的工作流、智库依据和模板推荐。";
    $("#ai-summary-risks").innerHTML = "<span>等待上下文同步</span>";
    renderAssistantPageAdvice();
    renderAssistantReviewSummary();
    renderAssistantGenerationSummary();
    renderAssistantScoreBoard();
    renderAssistantTaskSummary();
    renderAssistantRagEvidence();
    renderAssistantQuickActions();
    return;
  }
  const stepLabel = currentStepSummary()?.title || fmtStepName(state.activeStep);
  $("#assistant-context-label").textContent = `${activeNavLabel()} · ${currentProject()?.name || "围绕当前项目回答"} · ${stepLabel}`;
  $("#ai-summary-state").textContent = `${summary.state_label || "当前状态"} · ${summary.next_action?.title || "查看建议"}`;
  $("#ai-summary-text").textContent = summary.summary || "暂无摘要。";
  renderAssistantPageAdvice();
  renderAssistantReviewSummary();
  renderAssistantGenerationSummary();
  renderAssistantTaskSummary();
  renderAssistantRagEvidence();
  renderAssistantScoreBoard();
  renderAssistantQuickActions();
  const risks = summary.risks || [];
  $("#ai-summary-risks").innerHTML = risks.length
    ? risks.map((item) => `<span>${escapeHtml(item)}</span>`).join("")
    : "<span>暂无明显高风险项</span>";
}

function renderAssistantReviewSummary() {
  const container = $("#assistant-review-summary");
  const level = $("#assistant-review-level");
  if (!container || !level) return;
  const stats = state.workflowSummary?.stats || {};
  const aiReview = state.workflowSummary?.ai_review_report || {};
  const aiMetrics = aiReview.metrics || {};
  const wordStats = state.wordSummary?.stats || {};
  const consistency = state.wordSummary?.outline_consistency || {};
  const reviewItems = state.workflowSummary?.review_items || [];
  const scoreCount = Number(stats.score_points || 0);
  const confirmedScoreCount = (state.workflowSummary?.score_points || []).filter((item, index) => isScorePointConfirmed(item, index)).length;
  const coverageItems = buildScorePointCoverageItems();
  const hasOutline = Boolean((state.workflowSummary?.outline_preview || []).length);
  const coveredScoreCount = aiReview.schema_version === "ai_review_report_v1"
    ? Number(aiMetrics.score_points_covered || 0)
    : hasOutline
    ? coverageItems.filter((item) => item.statusKey === "covered").length
    : confirmedScoreCount;
  const coverageRiskCount = aiReview.schema_version === "ai_review_report_v1"
    ? Number(aiMetrics.score_points_risk || 0)
    : coverageItems.filter((item) => item.statusKey === "risk" || item.statusKey === "uncovered").length;
  const weakCount = Number(aiMetrics.chapters_pending || 0) + Number(wordStats.placeholder_count || 0) + Number(wordStats.missing_image_count || 0);
  const wordRisk = consistency.heading_count_matched === false ? 1 : 0;
  const manualCount = aiReview.schema_version === "ai_review_report_v1"
    ? Number(aiMetrics.manual_review_items || 0) + wordRisk
    : Math.max(Number(stats.review_items || 0) + wordRisk, coverageRiskCount);
  level.textContent = aiReview.level_label || (manualCount || weakCount ? "需要复核" : scoreCount ? "状态良好" : "等待数据");
  level.className = aiReview.level === "ok" ? "ok" : aiReview.level === "waiting" ? "warn" : manualCount || weakCount ? "warn" : "ok";
  container.innerHTML = `
    ${assistantMetricCard("评分点覆盖", scoreCount ? `${coveredScoreCount}/${scoreCount}` : "待解析", scoreCount ? (hasOutline || aiReview.schema_version ? "目录/正文承接" : "已确认 / 总数") : "上传后开始识别")}
    ${assistantMetricCard("空弱章节", fmtNumber(weakCount), "待生成、占位或缺图")}
    ${assistantMetricCard("表格图片风险", fmtNumber(Number(wordStats.missing_image_count || 0)), "缺失图片优先复核")}
    ${assistantMetricCard("Word 格式风险", wordRisk ? "需检查" : "暂无明显风险", "标题和目录一致性")}
    ${assistantMetricCard("人工确认项", fmtNumber(manualCount || reviewItems.length), "阻塞项先处理")}
  `;
}

function renderAssistantGenerationSummary() {
  const container = $("#assistant-generation-summary");
  const level = $("#assistant-generation-status");
  if (!container || !level) return;
  const report = state.workflowSummary?.generation_report || {};
  const metrics = report.metrics || {};
  const latestJob = report.latest_job || {};
  const status = String(report.status || "waiting");
  const statusClass = assistantGenerationStatusClass(status);
  level.textContent = report.status_label || "等待生成";
  level.className = statusClass;
  if (!report.available) {
    container.innerHTML = '<div class="mini-empty compact">正文生成或 Word 刷新后，小智会汇总耗时、token、失败项和下一步。</div>';
    return;
  }
  const duration = metrics.duration_seconds ? fmtDurationSeconds(metrics.duration_seconds) : "暂未记录";
  const stageTimings = assistantStageTimingItems(metrics.stage_timings || []);
  const primaryTiming = assistantPrimaryTiming(stageTimings, duration);
  const tokenText = metrics.token_estimate_available
    ? `约 ${fmtNumber(metrics.estimated_total_tokens)}`
    : "暂无精确统计";
  const nextAction = Array.isArray(report.next_actions) && report.next_actions.length
    ? report.next_actions[0]
    : "建议抽查重点章节、评分点响应和 Word 格式。";
  container.innerHTML = `
    <p class="assistant-generation-line">
      正文 ${fmtNumber(metrics.chapters_generated || 0)}/${fmtNumber(metrics.chapters_total || 0)} 已生成，
      ${metrics.word_ready ? "Word 初稿已就绪" : "Word 初稿待刷新"}，${escapeHtml(primaryTiming)}。
    </p>
    ${stageTimings.length ? `<div class="assistant-stage-timings">${stageTimings.map(renderAssistantStageTiming).join("")}</div>` : ""}
    <div class="assistant-generation-metrics">
      ${assistantMetricCard("任务总耗时", duration, latestJob.job_label || "最近任务")}
      ${assistantMetricCard("Token", tokenText, `${fmtNumber(metrics.llm_call_count || 0)} 次调用`)}
      ${assistantMetricCard("正文", `${fmtNumber(metrics.chapters_generated || 0)}/${fmtNumber(metrics.chapters_total || 0)}`, `${fmtNumber(metrics.chapters_failed || 0)} 个失败`)}
      ${assistantMetricCard("Word", metrics.word_ready ? "已就绪" : "待刷新", metrics.word_ready ? "可复核" : "先刷新初稿")}
    </div>
    <p class="assistant-generation-advice">${escapeHtml(nextAction)}</p>
  `;
}

function assistantStageTimingItems(timings = []) {
  return (timings || [])
    .filter((item) => item && item.key !== "upload" && Number(item.duration_seconds || 0) > 0)
    .map((item) => ({
      key: item.key,
      label: item.label || "步骤",
      duration: item.duration_label || fmtDurationSeconds(item.duration_seconds) || "待记录",
      status: item.status,
    }))
    .slice(0, 6);
}

function assistantPrimaryTiming(items, fallbackDuration) {
  const generation = items.find((item) => item.key === "chapter_llm_generation");
  const word = items.find((item) => item.key === "chapter_aggregate_refresh");
  if (generation && word) {
    return `模型生成 ${generation.duration}，Word 整理 ${word.duration}`;
  }
  if (generation) return `模型生成 ${generation.duration}`;
  if (word) return `Word 整理 ${word.duration}（不调用大模型）`;
  return `任务总耗时 ${fallbackDuration}`;
}

function renderAssistantStageTiming(item = {}) {
  return `
    <span class="${timingStatusClass(item.status)}">
      <i>${escapeHtml(item.label)}</i>
      <strong>${escapeHtml(item.duration)}</strong>
    </span>
  `;
}

function assistantGenerationStatusClass(status) {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "risk";
  if (status === "running") return "running";
  return "warn";
}

function renderWorkflowTiming(timings = []) {
  const panel = $("#workflow-timing-panel");
  const list = $("#workflow-timing-list");
  const status = $("#workflow-timing-status");
  if (!panel || !list || !status) return;
  const items = (timings || []).filter((item) => item && item.key !== "upload");
  if (!items.length) {
    panel.classList.add("hidden");
    list.innerHTML = "";
    status.textContent = "等待记录";
    return;
  }
  panel.classList.remove("hidden");
  const recorded = items.filter((item) => Number(item.duration_seconds || 0) > 0).length;
  status.textContent = recorded ? `已记录 ${recorded}/${items.length} 步` : "等待记录";
  list.innerHTML = items.map(renderWorkflowTimingItem).join("");
}

function renderWorkflowTimingItem(item = {}) {
  const duration = item.duration_label || fmtDurationSeconds(item.duration_seconds) || "待记录";
  const statusClass = timingStatusClass(item.status);
  const note = timingShortNote(item);
  return `
    <span class="workflow-timing-item ${statusClass}">
      <i>${escapeHtml(item.label || "步骤")}</i>
      <strong>${escapeHtml(duration)}</strong>
      <em>${escapeHtml(note)}</em>
    </span>
  `;
}

function timingStatusClass(status) {
  if (["succeeded", "completed", "available"].includes(String(status || ""))) return "done";
  if (["pending", "running"].includes(String(status || ""))) return "running";
  if (String(status || "") === "failed") return "failed";
  return "missing";
}

function timingShortNote(item = {}) {
  const status = item.status_label || "";
  const source = item.source || "";
  if (source) return `${status} · ${source}`;
  return status || "暂无";
}

function renderAssistantTaskSummary() {
  const container = $("#assistant-task-summary");
  const level = $("#assistant-task-status");
  if (!container || !level) return;
  const jobs = state.workflowSummary?.latest_jobs || [];
  const activeJob = jobs.find(isActiveJob);
  const latest = activeJob || jobs[0];
  const preflightItems = assistantQueuePreflightItems(latest, activeJob);
  if (!latest) {
    level.textContent = "空闲";
    level.className = "ok";
    container.innerHTML = `
      <div class="mini-empty">当前没有任务记录。创建解析、目录或正文任务后，助手会跟踪状态。</div>
      ${
        preflightItems.length
          ? `<div class="assistant-task-preflight">${preflightItems.map((item) => assistantTaskPreflightChip(item)).join("")}</div>`
          : ""
      }
    `;
    return;
  }
  const percent = Math.max(0, Math.min(100, Number(latest.progress_percent ?? (jobStatus(latest) === "succeeded" ? 100 : 0))));
  const failed = Number(latest.progress_failed || latest.metadata?.failed_count || 0);
  const status = jobStatus(latest);
  level.textContent = activeJob ? "执行中" : (latest.status_label || fmtJobStatus(status));
  level.className = activeJob ? "running" : failed || status === "failed" ? "risk" : "ok";
  const tip = activeJob
    ? "先等待当前任务结束，避免重复提交同类任务。"
    : failed || status === "failed"
    ? "建议先查看错误摘要，必要时只重试失败小节。"
    : "可以继续按五步流程推进。";
  container.innerHTML = `
    <div class="assistant-task-line">
      <strong>${escapeHtml(latest.job_label || latest.job_type || "最近任务")}</strong>
      <span>${escapeHtml(latest.message || latest.status_label || "状态已更新")}</span>
    </div>
    <div class="assistant-task-meta">
      <span>${escapeHtml(percent.toFixed(percent % 1 ? 1 : 0))}%</span>
      ${latest.progress_total ? `<span>${escapeHtml(latest.progress_completed || 0)} / ${escapeHtml(latest.progress_total)}</span>` : ""}
      ${failed ? `<span>失败 ${escapeHtml(failed)}</span>` : ""}
      <span>${escapeHtml(fmtTime(latest.updated_at))}</span>
    </div>
    ${
      preflightItems.length
        ? `<div class="assistant-task-preflight">${preflightItems.map((item) => assistantTaskPreflightChip(item)).join("")}</div>`
        : ""
    }
    <p>${escapeHtml(tip)}</p>
  `;
}

function assistantTaskPreflightChip(item) {
  return `
    <span class="${escapeHtml(item.tone || "pending")}">
      <i>${escapeHtml(item.label)}</i>
      <strong>${escapeHtml(item.value)}</strong>
    </span>
  `;
}

function assistantQueuePreflightItems(latest, activeJob) {
  const items = [];
  const step = String(state.activeStep || "");
  const generationUnits = state.workflowSummary?.generation_units || [];
  const unfinishedCount = generationUnits.filter(isUnfinishedGenerationUnit).length;
  const failedCount = generationUnits.filter(isFailedGenerationUnit).length;
  if (activeJob) {
    items.push({
      label: "队列",
      value: "当前已有任务在执行，先别重复提交同类任务。",
      tone: "pending",
    });
  }
  if (step === "parse") {
    items.push({
      label: "解析前",
      value: "先确认上传的是最新招标文件，避免重复解析旧版本。",
      tone: "ok",
    });
    if (Number(state.workflowSummary?.stats?.review_items || 0) > 0) {
      items.push({
        label: "复核项",
        value: "评分点有复核项，建议先处理再排队进入目录生成。",
        tone: "warn",
      });
    }
  } else if (step === "outline") {
    items.push({
      label: "目录前",
      value: "先确认评分点已对齐，目录不要覆盖已人工调整的内容。",
      tone: "ok",
    });
  } else if (step === "generate") {
    items.push({
      label: "生成前",
      value: unfinishedCount ? `优先挑 ${Math.min(3, unfinishedCount)} 个典型小节试跑。` : "当前没有待生成小节包。",
      tone: "ok",
    });
    if (failedCount) {
      items.push({
        label: "失败包",
        value: `有 ${fmtNumber(failedCount)} 个失败小节，建议先重试失败项。`,
        tone: "warn",
      });
    }
    const methodAdvice = generationMethodAdviceForChapter(selectedGenerationUnitTitle());
    items.push({
      label: "策略",
      value: methodAdvice.title,
      tone: methodAdvice.level === "rag" ? "ok" : methodAdvice.level === "original" ? "pending" : "warn",
    });
  } else if (step === "review") {
    items.push({
      label: "复核前",
      value: "先刷新 Word 初稿，再检查目录页码、表格图片和页眉页脚。",
      tone: "warn",
    });
  } else {
    items.push({
      label: "起步",
      value: "先上传招标文件，参考资料另行入库更清楚。",
      tone: "ok",
    });
  }
  return items.slice(0, 3);
}

function renderAssistantRagEvidence() {
  const container = $("#assistant-rag-evidence");
  const level = $("#assistant-rag-status");
  if (!container || !level) return;
  const materials = sortAssistantEvidenceItems(state.ragMaterials?.results || []).slice(0, 3);
  const strategy = generationMethodAdviceForChapter(selectedGenerationUnitTitle() || currentStepSummary()?.title || state.activeStep || "");
  level.textContent = materials.length ? `${materials.length} 条依据` : "待检索";
  level.className = materials.length ? "ok" : "warn";
  if (!state.selectedProjectId) {
    container.innerHTML = '<div class="mini-empty">选择项目后，助手会结合章节检索参考资料依据。</div>';
    return;
  }
  if (!materials.length) {
    container.innerHTML = `
      <div class="assistant-rag-strategy ${escapeHtml(strategy.level)}">
        <strong>生成策略：${escapeHtml(strategy.title)}</strong>
        <span>${escapeHtml(strategy.reason)}</span>
        <small>${escapeHtml(strategy.hint)}</small>
      </div>
      <div class="mini-empty">当前章节还没有命中参考资料。可在投标知识库补充法规、规范、企业制度、评审办法或优秀标书。</div>
    `;
    return;
  }
  container.innerHTML = `
    <div class="assistant-rag-strategy ${escapeHtml(strategy.level)}">
      <strong>生成策略：${escapeHtml(strategy.title)}</strong>
      <span>${escapeHtml(strategy.reason)}</span>
      <small>${escapeHtml(strategy.hint)}</small>
    </div>
    <div class="assistant-rag-list">
      ${materials
        .map((item) => {
          const title = item.title || item.section_title || "未命名依据";
          const sourceTitle = item.source_title || "投标知识库";
          const pageRange = formatAssistantPageRange(item.start_page, item.end_page);
          const pathText = Array.isArray(item.section_path) && item.section_path.length ? item.section_path.join(" > ") : "";
          const citation = [item.knowledge_type_label || fmtKnowledgeType(item.knowledge_type), `《${sourceTitle}》`, pageRange]
            .filter(Boolean)
            .join(" · ");
          return `
            <article>
              <strong>${escapeHtml(title)}</strong>
              <span>${escapeHtml(citation)}${pathText ? ` · ${escapeHtml(pathText)}` : ""}</span>
              <small>${escapeHtml(item.reason || item.text_preview || "可作为当前章节写作或风险复核依据。")}</small>
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}

function sortAssistantEvidenceItems(items = []) {
  const priority = {
    law_regulation: 0,
    technical_standard: 1,
    enterprise_policy: 2,
    review_rule: 3,
    excellent_bid: 4,
    other: 5,
  };
  return [...items].sort((a, b) => {
    const aRank = priority[a?.knowledge_type] ?? 6;
    const bRank = priority[b?.knowledge_type] ?? 6;
    if (aRank !== bRank) return aRank - bRank;
    return Number(b?.score || 0) - Number(a?.score || 0);
  });
}

function formatAssistantPageRange(startPage, endPage) {
  const start = Number(startPage || 0);
  const end = Number(endPage || 0);
  if (start > 0 && end > 0 && end !== start) {
    return `第 ${start}-${end} 页`;
  }
  if (start > 0) {
    return `第 ${start} 页`;
  }
  return "";
}

function assistantMetricCard(label, value, hint) {
  return `
    <span>
      <i>${escapeHtml(label)}</i>
      <strong>${escapeHtml(value)}</strong>
      <em>${escapeHtml(hint)}</em>
    </span>
  `;
}

function buildScorePointCoverageItems() {
  const backendCoverage = state.workflowSummary?.score_point_coverage;
  if (backendCoverage?.schema_version === "score_point_coverage_v1" && Array.isArray(backendCoverage.items)) {
    return backendCoverage.items.map((item, index) => normalizeBackendCoverageItem(item, index));
  }
  const scorePoints = state.workflowSummary?.score_points || [];
  const outlineItems = flattenOutlinePreview(state.workflowSummary?.outline_preview || []);
  const generationUnits = state.workflowSummary?.generation_units || [];
  const reviewCount = Number(state.workflowSummary?.stats?.review_items || 0);
  return scorePoints.map((scorePoint, index) => {
    const outlineMatch = findOutlineMatchForScorePoint(scorePoint, outlineItems);
    const generationMatches = findGenerationMatchesForScorePoint(scorePoint, outlineMatch, generationUnits);
    return scorePointCoverageStatus({
      scorePoint,
      index,
      outlineMatch,
      generationMatches,
      hasOutline: outlineItems.length > 0,
      hasGenerationUnits: generationUnits.length > 0,
      reviewRequired: String(scorePoint?.status || "").includes("复核") || index < reviewCount,
    });
  });
}

function normalizeBackendCoverageItem(item, index) {
  const statusKey = String(item?.status_key || "pending");
  const statusClass = statusKey === "covered" ? "ok" : statusKey === "risk" || statusKey === "uncovered" ? "risk" : "pending";
  return {
    scorePoint: {
      title: item?.title || `评分点 ${index + 1}`,
      score: item?.score,
    },
    index: Number.isFinite(Number(item?.index)) ? Number(item.index) : index,
    outlineText: item?.outline_text || "目录待生成",
    generationText: item?.generation_text || "正文待生成",
    statusKey,
    label: item?.status_label || (statusKey === "covered" ? "已覆盖" : statusKey === "uncovered" ? "未覆盖" : statusKey === "risk" ? "需复核" : "待确认"),
    statusClass,
  };
}

function flattenOutlinePreview(nodes = [], parentTitles = [], depth = 1) {
  const result = [];
  for (const node of nodes || []) {
    if (!node || typeof node !== "object") continue;
    const title = String(node.title || "").trim();
    const pathTitles = [...parentTitles, title].filter(Boolean);
    const children = Array.isArray(node.children) ? node.children : [];
    result.push({
      node,
      nodeId: node.node_id,
      title,
      depth,
      pathTitles,
      pathText: pathTitles.join(" > "),
      descendantNodeIds: collectOutlineNodeIds(node),
    });
    result.push(...flattenOutlinePreview(children, pathTitles, depth + 1));
  }
  return result;
}

function collectOutlineNodeIds(node) {
  if (!node || typeof node !== "object") return [];
  const ids = node.node_id ? [String(node.node_id)] : [];
  for (const child of node.children || []) {
    ids.push(...collectOutlineNodeIds(child));
  }
  return ids;
}

function normalizeMatchText(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/^\s*\d+(\.\d+)*[、.．\s-]*/g, "")
    .replace(/[（）()【】\[\]《》<>、，。；;：:！!？?\s"'“”‘’\-_.·]/g, "");
}

function matchSegments(text) {
  return String(text || "")
    .split(/[，。；;：:、\s"'“”‘’（）()【】\[\]《》<>]+/)
    .map(normalizeMatchText)
    .filter((item) => item.length >= 3)
    .slice(0, 8);
}

function textMatchScore(sourceText, targetText) {
  const source = normalizeMatchText(sourceText);
  const target = normalizeMatchText(targetText);
  if (!source || !target) return 0;
  if (source === target) return 1;
  if (source.length >= 4 && target.includes(source)) return 0.92;
  if (target.length >= 4 && source.includes(target)) return 0.82;

  const sourceSegments = matchSegments(sourceText);
  const targetSegments = matchSegments(targetText);
  const segmentHits = sourceSegments.filter((segment) => target.includes(segment)).length
    + targetSegments.filter((segment) => source.includes(segment)).length;
  if (segmentHits) {
    return Math.min(0.78, segmentHits / Math.max(2, sourceSegments.length + targetSegments.length) + 0.34);
  }

  const sourceChars = new Set(source.split("").filter((char) => /[\u4e00-\u9fa5a-z0-9]/.test(char)));
  const targetChars = new Set(target.split("").filter((char) => /[\u4e00-\u9fa5a-z0-9]/.test(char)));
  if (sourceChars.size < 6 || targetChars.size < 6) return 0;
  const overlap = [...sourceChars].filter((char) => targetChars.has(char)).length;
  return (overlap / Math.min(sourceChars.size, targetChars.size)) * 0.56;
}

function findOutlineMatchForScorePoint(scorePoint, outlineItems) {
  let best = null;
  for (const item of outlineItems || []) {
    const titleScore = textMatchScore(scorePoint?.title, item.title);
    const pathScore = textMatchScore(scorePoint?.title, item.pathText) * 0.96;
    const depthBonus = item.depth === 1 ? 0.08 : 0;
    const score = Math.max(titleScore, pathScore) + depthBonus;
    if (!best || score > best.score) {
      best = { ...item, score };
    }
  }
  return best && best.score >= 0.48 ? best : null;
}

function findGenerationMatchesForScorePoint(scorePoint, outlineMatch, generationUnits) {
  const matches = [];
  const seen = new Set();
  const nodeIds = new Set((outlineMatch?.descendantNodeIds || []).map(String));
  const outlineTopTitle = outlineMatch?.pathTitles?.[0] || outlineMatch?.title || "";
  const outlineText = outlineMatch?.pathText || "";

  for (const [index, unit] of (generationUnits || []).entries()) {
    const key = String(unit.unit_id || `${unit.target_node_id || ""}:${index}`);
    const targetId = String(unit.target_node_id || "");
    const chapterPath = Array.isArray(unit.chapter_path) ? unit.chapter_path.filter(Boolean).join(" > ") : "";
    const chapterText = `${chapterPath} ${unit.chapter || ""}`;
    const nodeMatched = targetId && nodeIds.has(targetId);
    const textMatched = outlineMatch
      ? textMatchScore(outlineTopTitle || outlineText, chapterText) >= 0.48 || textMatchScore(outlineText, chapterText) >= 0.48
      : textMatchScore(scorePoint?.title, chapterText) >= 0.5;
    if ((nodeMatched || textMatched) && !seen.has(key)) {
      seen.add(key);
      matches.push(unit);
    }
  }
  return matches;
}

function scorePointCoverageStatus({ scorePoint, index, outlineMatch, generationMatches, hasOutline, hasGenerationUnits, reviewRequired }) {
  const confirmed = isScorePointConfirmed(scorePoint, index);
  const generatedCount = generationMatches.filter(isGeneratedGenerationUnit).length;
  const failedCount = generationMatches.filter(isFailedGenerationUnit).length;
  const totalGeneration = generationMatches.length;
  const outlineText = outlineMatch?.pathText || (hasOutline ? "未找到承接目录" : "目录待生成");
  let statusKey = "pending";
  let label = confirmed ? "待确认" : "待确认";
  let generationText = hasGenerationUnits ? "正文待匹配" : "正文待生成";

  if (reviewRequired && !confirmed) {
    statusKey = "risk";
    label = "需复核";
  }
  if (hasOutline && !outlineMatch) {
    statusKey = "uncovered";
    label = "未覆盖";
  }
  if (outlineMatch && !hasGenerationUnits) {
    statusKey = confirmed ? "pending" : "pending";
    label = "目录已承接";
    generationText = "正文待生成";
  }
  if (outlineMatch && hasGenerationUnits) {
    if (!totalGeneration) {
      statusKey = "pending";
      label = confirmed ? "待确认" : "待确认";
      generationText = "目录已承接，正文待匹配";
    } else if (failedCount) {
      statusKey = "risk";
      label = "需复核";
      generationText = `已生成 ${generatedCount}/${totalGeneration}，失败 ${failedCount}`;
    } else if (generatedCount === totalGeneration) {
      statusKey = "covered";
      label = "已覆盖";
      generationText = `已生成 ${generatedCount}/${totalGeneration}`;
    } else {
      statusKey = "pending";
      label = confirmed ? "待生成" : "待确认";
      generationText = `已生成 ${generatedCount}/${totalGeneration}`;
    }
  }

  return {
    scorePoint,
    index,
    outlineText,
    generationText,
    statusKey,
    label,
    statusClass: statusKey === "covered" ? "ok" : statusKey === "risk" || statusKey === "uncovered" ? "risk" : "pending",
  };
}

function renderAssistantScoreBoard() {
  const board = $("#assistant-score-board");
  const status = $("#assistant-score-status");
  if (!board || !status) return;
  const items = state.workflowSummary?.score_points || [];
  if (!items.length) {
    status.textContent = "待解析";
    board.innerHTML = '<div class="mini-empty">上传招标文件后，AI 助手会开始分析评分点。</div>';
    return;
  }
  const coverageItems = buildScorePointCoverageItems();
  const covered = coverageItems.filter((item) => item.statusKey === "covered");
  const pending = coverageItems.filter((item) => item.statusKey === "pending");
  const risk = coverageItems.filter((item) => item.statusKey === "risk" || item.statusKey === "uncovered");
  status.textContent = `已覆盖 ${covered.length} · 待确认 ${pending.length} · 风险 ${risk.length}`;
  board.innerHTML = `
    <div class="assistant-score-metrics">
      <span class="ok"><strong>${covered.length}</strong><small>已覆盖</small></span>
      <span class="pending"><strong>${pending.length}</strong><small>待确认</small></span>
      <span class="risk"><strong>${risk.length}</strong><small>未覆盖/复核</small></span>
    </div>
    <div class="assistant-score-list">
      ${coverageItems.slice(0, 5).map((coverage) => {
        const item = coverage.scorePoint || {};
        return `
          <article class="${coverage.statusClass}">
            <div class="assistant-score-title-row">
              <strong>${escapeHtml(item.title || `评分点 ${coverage.index + 1}`)}</strong>
              <em class="${coverage.statusClass}">${escapeHtml(coverage.label)}</em>
            </div>
            <span>对应目录：${escapeHtml(coverage.outlineText)}</span>
            <span>正文状态：${escapeHtml(coverage.generationText)}</span>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderAssistantPageAdvice() {
  const target = $("#assistant-page-advice");
  if (!target) return;
  const advice = assistantAdviceForCurrentContext();
  target.innerHTML = `
    <strong>${escapeHtml(advice.title)}</strong>
    <span>${escapeHtml(advice.text)}</span>
  `;
}

function renderAssistantQuickActions() {
  const container = $(".assistant-quick-actions");
  if (!container) return;
  const actions = assistantQuickActionsForCurrentContext();
  container.innerHTML = actions.map((item) => `
    <button type="button" data-assistant-prompt="${escapeHtml(item.prompt)}">${escapeHtml(item.label)}</button>
  `).join("");
  container.querySelectorAll("[data-assistant-prompt]").forEach((button) => {
    button.addEventListener("click", askAssistantQuickQuestion);
  });
}

function assistantQuickActionsForCurrentContext() {
  if (state.activeView === "home") {
    return [
      { label: "平台说明", prompt: "智标工坊升级成智能招投标平台后，各模块分别负责什么？" },
      { label: "下一步", prompt: "现在应该优先完善哪个平台能力？" },
      { label: "编标入口", prompt: "技术标编制模块和平台 AI 助手怎么配合？" },
      { label: "能力边界", prompt: "当前平台哪些能力已经可用，哪些还需要接入？" },
    ];
  }
  if (state.activeView === "qa") {
    return [
      { label: "能问什么", prompt: "平台智能问答可以回答哪些招投标问题？" },
      { label: "依据来源", prompt: "智能问答会从哪些资料和数据源找依据？" },
      { label: "风险判断", prompt: "遇到企业风险或合规问题时应该怎么问？" },
      { label: "编标联动", prompt: "智能问答如何服务技术标编制？" },
    ];
  }
  if (state.activeView === "bid-intel") {
    return [
      { label: "标讯筛选", prompt: "标讯情报模块后续应该支持哪些筛选条件？" },
      { label: "机会判断", prompt: "看到一条招标公告后，应该从哪些角度判断是否值得投？" },
      { label: "数据源", prompt: "实时标讯数据源接入后应该注意哪些风险？" },
      { label: "下一步", prompt: "标讯情报模块下一步应该怎么完善？" },
    ];
  }
  if (state.activeView === "risk") {
    return [
      { label: "风险维度", prompt: "企业风险分析应该关注哪些维度？" },
      { label: "资质核验", prompt: "投标前怎么核验企业资质和失信风险？" },
      { label: "合规依据", prompt: "企业风险判断需要结合哪些法规或制度依据？" },
      { label: "下一步", prompt: "企业风险模块下一步应该怎么完善？" },
    ];
  }
  if (state.activeView === "suppliers") {
    return [
      { label: "推荐逻辑", prompt: "供应商推荐应该按哪些维度排序？" },
      { label: "风险过滤", prompt: "供应商推荐前需要先过滤哪些风险？" },
      { label: "数据来源", prompt: "供应商推荐需要哪些历史数据和企业数据？" },
      { label: "下一步", prompt: "供应商推荐模块下一步应该怎么完善？" },
    ];
  }
  if (state.activeView === "rag") {
    return [
      { label: "智库用法", prompt: "当前项目可用的智库依据有哪些？" },
      { label: "法规规范", prompt: "结合参考资料里的法规和规范，当前项目有哪些合规风险？" },
      { label: "制度口径", prompt: "企业制度类资料应该怎么参与正文和风险复核？" },
      { label: "评审办法", prompt: "评审办法类资料应该怎么用于评分点响应？" },
    ];
  }
  if (state.activeView === "templates") {
    const template = selectedBidTemplate();
    const templateName = template?.name || "当前模板";
    return [
      { label: "推荐模板", prompt: "当前项目推荐使用哪个投标模板？" },
      { label: "怎么用", prompt: `${templateName}应该怎么用于当前项目？` },
      { label: "差哪些", prompt: `${templateName}和当前项目评分点还差哪些内容？` },
      { label: "套用边界", prompt: "投标模板能不能直接覆盖当前目录？" },
      { label: "下一步", prompt: "这个模块下一步应该做什么？" },
    ];
  }
  if (state.activeView === "model") {
    return [
      { label: "服务影响", prompt: "AI 服务设置会影响哪些生成步骤？" },
      { label: "速度成本", prompt: "如何在速度和质量之间取舍？" },
      { label: "失败排查", prompt: "模型调用失败时应该先检查什么？" },
      { label: "下一步", prompt: "这个模块下一步应该做什么？" },
    ];
  }
  if (state.activeView === "accounts") {
    return [
      { label: "角色边界", prompt: "账户管理里的角色应该怎么划分？" },
      { label: "生产权限", prompt: "正式生产时账户权限还需要补哪些能力？" },
      { label: "安全提醒", prompt: "账户管理现在有哪些安全边界？" },
      { label: "下一步", prompt: "账户模块下一步应该怎么完善？" },
    ];
  }
  const byStep = {
    upload: [
      { label: "上传建议", prompt: "当前上传资料步骤要注意什么？" },
      { label: "下一步", prompt: "这个项目下一步应该做什么？" },
      { label: "智库依据", prompt: "当前项目可用的智库依据有哪些？" },
      { label: "风险", prompt: "当前有哪些需要人工注意的风险？" },
    ],
    parse: [
      { label: "评分点响应", prompt: "当前评分点响应情况怎么样？" },
      { label: "复核重点", prompt: "解析确认步骤有哪些需要人工复核？" },
      { label: "下一步", prompt: "这个项目下一步应该做什么？" },
      { label: "风险", prompt: "当前有哪些需要人工注意的风险？" },
    ],
    outline: [
      { label: "目录检查", prompt: "技术标目录现在应该检查哪些问题？" },
      { label: "评分点承接", prompt: "评分点和目录章节对应情况怎么样？" },
      { label: "模板", prompt: "当前项目推荐使用哪个投标模板？" },
      { label: "下一步", prompt: "这个项目下一步应该做什么？" },
    ],
    generate: [
      { label: "生成策略", prompt: "生成正文时原算法和参考资料分别适合负责什么？" },
      { label: "排队前检查", prompt: "正文生成任务排队前需要先确认哪些内容？" },
      { label: "生成小结", prompt: "本次生成耗时、token 和失败项怎么样？" },
      { label: "AI 复核报告", prompt: "生成 AI 复核报告摘要。" },
      { label: "智库依据", prompt: "当前项目可用的智库依据有哪些？" },
      { label: "合规风险", prompt: "结合参考资料里的法规和规范，当前项目有哪些合规风险？" },
    ],
    review: [
      { label: "生成小结", prompt: "本次生成耗时、token 和失败项怎么样？" },
      { label: "AI 复核报告", prompt: "生成 AI 复核报告摘要。" },
      { label: "Word 风险", prompt: "Word 初稿复核要重点检查什么？" },
      { label: "评分点响应", prompt: "当前评分点响应情况怎么样？" },
    ],
  };
  return byStep[state.activeStep] || [
    { label: "下一步", prompt: "这个项目下一步应该做什么？" },
    { label: "风险", prompt: "当前有哪些需要人工注意的风险？" },
    { label: "评分点响应", prompt: "当前评分点响应情况怎么样？" },
    { label: "AI 复核报告", prompt: "生成 AI 复核报告摘要。" },
  ];
}

function assistantAdviceForCurrentContext() {
  if (state.activeView !== "projects") {
    return assistantModuleAdvice(state.activeView);
  }
  return assistantStepAdvice(state.activeStep);
}

function assistantModuleAdvice(view) {
  const map = {
    home: {
      title: "平台首页说明",
      text: "这里会汇总智能问答、标讯情报、企业风险、供应商推荐和技术标编制入口。当前先保留稳定编标流程，平台能力逐步接入。",
    },
    qa: {
      title: "智能问答说明",
      text: "这里预留招投标综合问答能力。后续会接入问题拆分、多源检索和带依据回答，当前可先使用右侧平台 AI 助手。",
    },
    "bid-intel": {
      title: "标讯情报说明",
      text: "这里预留实时标讯检索和机会筛选。正式接入前不会影响现有技术标编制流程。",
    },
    risk: {
      title: "企业风险说明",
      text: "这里预留企业风险、资质核验和合规判断。风险结论后续必须带来源摘要，不能只给黑盒判断。",
    },
    suppliers: {
      title: "供应商推荐说明",
      text: "这里预留供应商检索和推荐排序。推荐理由会结合价格、距离、风险、历史合作和项目适配度。",
    },
    rag: {
      title: "投标知识库说明",
      text: "这里管理优秀标书、法律法规、技术规范、企业制度和评审办法。小助手回答风险、评分点和生成策略时会优先引用这些资料的摘要和来源。",
    },
    templates: {
      title: "投标模板说明",
      text: "这里沉淀企业常用目录和章节结构。第一版只做推荐和预览，不会自动覆盖你已经调整过的目录或正文。",
    },
    model: {
      title: "系统设置说明",
      text: "这里影响解析、目录补强、正文生成和小助手回答的 AI 服务。修改前建议确认当前没有正在生成的项目，避免影响编标过程。",
    },
    accounts: {
      title: "账户管理说明",
      text: "这里先维护企业内部账号、角色和启停状态。当前不强制登录，正式生产还需要接认证、项目权限和下载鉴权。",
    },
  };
  return map[view] || {
    title: "工作台说明",
    text: "小助手会根据当前页面解释功能边界，并提醒哪些动作需要人工确认。",
  };
}

function assistantStepAdvice(step) {
  const stats = state.workflowSummary?.stats || {};
  const map = {
    upload: {
      title: "第 1 步：上传资料",
      text: "重点是先上传招标文件，并确认文件版本正确。优秀标书、法规规范、企业制度和评审办法建议去投标知识库单独入库。",
    },
    parse: {
      title: "第 2 步：解析确认",
      text: `重点看评分点和复核项。当前识别评分点 ${fmtNumber(stats.score_points)} 个，复核项 ${fmtNumber(stats.review_items)} 个；有阻塞问题时先人工确认。`,
    },
    outline: {
      title: "第 3 步：生成目录",
      text: "重点检查一级目录是否严格覆盖评分点，二三级目录是否像真实技术标。用户手动改过的目录不要被自动覆盖。",
    },
    generate: {
      title: "第 4 步：生成正文",
      text: "重点先选典型章节试跑。结构、评分点、表格图片靠原算法稳住，措施写法、法规规范、企业制度和评审办法用智库依据增强。",
    },
    review: {
      title: "第 5 步：Word 复核",
      text: "重点检查标题层级、目录页码、表格图片、评分点响应和 OnlyOffice 保存结果。最终成稿必须人工确认。",
    },
  };
  return map[step] || {
    title: "编标项目说明",
    text: "主流程分为上传资料、解析确认、生成目录、生成正文和 Word 复核五步，小助手会跟随当前步骤提醒重点。",
  };
}

function renderAiInsightPanel() {
  const panel = $("#ai-insight-panel");
  if (!panel) return;
  const ai = state.aiSummary || {};
  const workflow = state.workflowSummary || {};
  const stats = workflow.stats || {};
  const nextAction = ai.next_action || {};
  const recommendedStep = recommendedActiveStep(workflow);
  const template = (state.bidTemplateRecommendation?.recommendations || [])[0];
  const title = ai.state_label
    ? `${ai.state_label} · ${nextAction.title || "查看项目状态"}`
    : "AI 正在读取项目状态";
  const text = ai.summary || "系统会结合招标解析、目录、参考资料和模板推荐，给出下一步编标建议。";
  const chips = [
    `评分点 ${stats.score_points ?? 0} 个`,
    `复核项 ${stats.review_items ?? 0} 个`,
    template ? `推荐模板：${template.name || "企业模板"}` : "模板待匹配",
    Number(stats.excellent_bid_files || 0) > 0 ? `参考资料 ${stats.excellent_bid_files} 份` : "参考资料待补充",
  ];
  const readiness = calculateAiReadiness(workflow, template);

  $("#ai-insight-title").textContent = title;
  $("#ai-insight-text").textContent = text;
  $("#ai-insight-stage").textContent = currentStepSummary()?.title || ai.state_label || "等待上下文";
  $("#ai-insight-stage").className = `status-pill ${Number(stats.review_items || 0) > 0 ? "pending" : "ok"}`;
  $("#ai-insight-chips").innerHTML = chips.map((item) => `<span>${escapeHtml(item)}</span>`).join("");
  $("#ai-readiness-score").textContent = `${readiness.score}%`;
  $("#ai-readiness-bar").style.width = `${readiness.score}%`;

  const button = $("#ai-go-next-step");
  if (button) {
    button.dataset.targetStep = recommendedStep;
    const currentLabel = currentStepSummary()?.title || fmtStepName(state.activeStep);
    const targetLabel = fmtStepName(recommendedStep);
    button.textContent = recommendedStep === state.activeStep
      ? `查看${currentLabel}重点`
      : `前往${targetLabel}`;
  }
}

function selectedGenerationUnitTitle() {
  const units = state.workflowSummary?.generation_units || [];
  const selectedId = [...state.selectedGenerationUnits][0];
  const selected = units.find((item) => String(item.unit_id || "") === String(selectedId || ""));
  if (selected) return generationUnitTitle(selected);
  const pending = units.find(isPendingGenerationUnit) || units[0];
  return pending ? generationUnitTitle(pending) : "";
}

function generationMethodAdviceForChapter(title = "") {
  const text = String(title || "").toLowerCase();
  const originalFirst = [
    "目录",
    "评分",
    "响应",
    "工程概况",
    "项目概况",
    "工期",
    "质量目标",
    "安全目标",
    "劳动力",
    "机械",
    "设备",
    "计划表",
    "表格",
    "组织机构",
    "管理人员",
  ];
  const ragFirst = [
    "施工方案",
    "技术措施",
    "质量保证",
    "安全文明",
    "进度措施",
    "重难点",
    "重点难点",
    "绿色施工",
    "bim",
    "成品保护",
    "风险",
    "合规",
    "法律",
    "条款",
    "法规",
    "规范",
    "制度",
    "评审",
    "办法",
  ];
  const mixed = [
    "施工部署",
    "资源配置",
    "总平面",
    "平面布置",
    "季节性",
    "专项方案",
    "应急",
    "文明施工",
    "环保",
  ];
  if (originalFirst.some((keyword) => text.includes(keyword))) {
    return {
      level: "original",
      title: "原算法优先",
      reason: "该章节更依赖评分点、目录路径、工程基础信息、表格或图片等结构化约束。",
      hint: "参考资料只做补充，不建议让素材改写硬性数据和格式。",
    };
  }
  if (ragFirst.some((keyword) => text.includes(keyword))) {
    return {
      level: "rag",
      title: "参考资料增强优先",
      reason: "该章节需要成熟写法、类似项目经验、规范依据和风险措施补充。",
      hint: "先用原算法锁定章节边界，再用参考资料丰富表达和措施。",
    };
  }
  if (mixed.some((keyword) => text.includes(keyword))) {
    return {
      level: "mixed",
      title: "混合使用",
      reason: "该章节既需要项目化结构，又需要参考历史写法和规范措施。",
      hint: "原算法负责骨架，参考资料负责补材料、补依据、补表达。",
    };
  }
  return {
    level: "mixed",
    title: "混合使用",
    reason: "当前章节未命中强规则，建议保持原流程生成，同时展示参考资料作为人工参考。",
    hint: "生成结果仍以评分点和项目上下文为准。",
  };
}

function calculateAiReadiness(summary = state.workflowSummary || {}, template = null) {
  const stats = summary.stats || {};
  const artifacts = summary.artifacts || {};
  const checks = [
    Number(stats.tender_files || 0) > 0,
    Number(stats.score_points || 0) > 0,
    Boolean(artifacts.outline?.exists || artifacts.outline),
    Number(stats.estimated_chapters || 0) > 0 || Boolean(artifacts.chapter_inputs?.exists || artifacts.chapter_inputs),
    Boolean(template),
    Number(stats.excellent_bid_files || 0) > 0 || Number(state.excellentBidLibrary?.source_count || 0) > 0,
  ];
  const score = Math.round((checks.filter(Boolean).length / checks.length) * 100);
  return { score, checks };
}

function recommendedStepFromAction(actionKey = "", currentState = "") {
  const text = `${actionKey} ${currentState}`.toLowerCase();
  if (text.includes("upload") || text.includes("empty")) return "upload";
  if (text.includes("parse") || text.includes("score") || text.includes("files_uploaded")) return "parse";
  if (text.includes("outline")) return "outline";
  if (text.includes("chapter") || text.includes("generation")) return "generate";
  if (text.includes("word") || text.includes("review") || text.includes("final")) return "review";
  return state.activeStep || "upload";
}

function fmtStepName(step) {
  const map = {
    upload: "上传资料",
    parse: "解析确认",
    outline: "技术标目录",
    generate: "正文生成",
    review: "Word 初稿",
  };
  return map[step] || "建议步骤";
}

function assistantChatInput(form = null) {
  const chatForm = form || $("#assistant-chat-form");
  return chatForm?.elements?.message || chatForm?.querySelector?.('input[name="message"]') || null;
}

function setAssistantChatPending(pending, message = "") {
  state.assistantAnswerPending = pending;
  const form = $("#assistant-chat-form");
  const input = assistantChatInput(form);
  const submit = form?.querySelector('button[type="submit"]');
  const answer = $("#assistant-chat-answer");
  const quickButtons = $$(".assistant-quick-actions button");
  if (input) input.disabled = pending;
  if (submit) submit.disabled = pending;
  quickButtons.forEach((button) => {
    button.disabled = pending;
  });
  form?.classList.toggle("is-pending", pending);
  if (answer && message) {
    answer.className = pending ? "assistant-answer pending" : "assistant-answer";
    answer.innerHTML = pending
      ? `<div class="assistant-thinking"><span aria-hidden="true"></span>${escapeHtml(message)}</div>`
      : escapeHtml(message);
    answer.scrollIntoView({ block: "nearest" });
  }
}

async function askAssistant(event) {
  event.preventDefault();
  if (state.assistantAnswerPending) return;
  const input = assistantChatInput(event.currentTarget);
  await submitAssistantQuestion((input?.value || "").trim(), input);
}

async function askAssistantQuickQuestion(event) {
  if (state.assistantAnswerPending) return;
  const message = event.currentTarget.dataset.assistantPrompt || "";
  const input = assistantChatInput();
  if (input) input.value = message;
  await submitAssistantQuestion(message, input);
}

async function submitAssistantQuestion(message, input = null) {
  const answer = $("#assistant-chat-answer");
  if (!message) {
    toast("请输入要询问的问题");
    return;
  }
  const projectId = state.selectedProjectId || null;
  const seq = ++state.assistantChatSeq;
  const hasProjectContext = Boolean(projectId);
  setAssistantChatPending(
    true,
    hasProjectContext
      ? "小智正在思考中，正在读取当前项目状态和投标知识库依据..."
      : "小智正在思考中，正在梳理平台上下文和投标知识库依据..."
  );
  const contextRefresh = hasProjectContext
    ? refreshProjectContext({
        silent: true,
        refreshFiles: false,
        refreshWorkflow: true,
        refreshAssistant: true,
      }).catch((error) => {
        console.warn("assistant context refresh failed", error);
      })
    : Promise.resolve();
  try {
    if ((state.selectedProjectId || null) !== projectId) {
      toast("上下文已变化，请重新提问");
      return;
    }
    const result = await api("/api/v1/assistant/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        active_view: state.activeView,
        active_step: state.activeStep,
        project_id: projectId,
        selected_template_id: state.selectedBidTemplateId || null,
        account_id: state.currentAccount?.account_id || null,
        account_display_name: state.currentAccount?.display_name || null,
        account_role: state.currentAccount?.role || null,
        account_role_label: state.currentAccount?.role_label || null,
      }),
    });
    if (seq !== state.assistantChatSeq || (state.selectedProjectId || null) !== projectId) return;
    if (input) input.value = "";
    if (answer) {
      answer.className = "assistant-answer";
      answer.innerHTML = `
        ${renderAssistantIntentMeta(result)}
        <strong class="assistant-answer-main">${escapeHtml(result.answer || "暂无回答。")}</strong>
        ${renderAssistantBoundaryLine(result, hasProjectContext)}
        ${renderAssistantAnswerThinking(result)}
      `;
      answer.scrollIntoView({ block: "nearest" });
    }
  } catch (error) {
    if (seq === state.assistantChatSeq && answer) {
      answer.className = "assistant-answer error";
      answer.innerHTML = `<strong>助手回答失败。</strong><small>${escapeHtml(userFacingErrorMessage(error))}</small>`;
      answer.scrollIntoView({ block: "nearest" });
    }
  } finally {
    if (seq === state.assistantChatSeq) {
      setAssistantChatPending(false);
      void contextRefresh;
    }
  }
}

function renderAssistantBoundaryLine(result = {}, hasProjectContext = false) {
  const blocked = Array.isArray(result.blocked_actions) ? result.blocked_actions.filter(Boolean) : [];
  if (blocked.length) {
    return `<small class="assistant-boundary-line">受控边界：${escapeHtml(blocked.slice(0, 2).join("；"))}</small>`;
  }
  const fallback = hasProjectContext
    ? "助手读取当前项目上下文回答，不执行上传、解析、生成、覆盖或删除操作。"
    : "助手可回答平台能力、投标知识库、模板和通用招投标问题；具体编标问题需要选择项目。";
  return `<small class="assistant-boundary-line">${escapeHtml(fallback)}</small>`;
}

function renderAssistantIntentMeta(result = {}) {
  const isFallback = result.intent === "fallback";
  if (!isFallback) return "";
  return `
    <div class="assistant-intent-line fallback">
      <span>暂不确定</span>
      <em>请围绕平台能力、投标知识库、模板或当前编标项目提问</em>
    </div>
  `;
}

function renderAssistantAnswerThinking(result = {}) {
  let evidence = [];
  try {
    evidence = mergeAssistantAnswerEvidence(result);
  } catch (error) {
    console.warn("assistant thinking evidence render failed", error);
    evidence = [];
  }
  if (!evidence.length) return "";
  return `
    <details class="assistant-thinking-panel">
      <summary>
        <span>查看思考过程与依据</span>
        <small>${fmtNumber(evidence.length)} 条</small>
      </summary>
      <div class="assistant-thinking-body">
        <p>小智结合当前项目状态和可用资料生成回答，以下依据仅供人工复核。</p>
        ${renderAssistantAnswerEvidenceList(evidence)}
      </div>
    </details>
  `;
}

function renderAssistantAnswerEvidenceList(evidence = []) {
  if (!evidence.length) return "";
  return `
    <div class="assistant-answer-evidence">
      ${evidence
        .map((item, index) => `
          <span class="${escapeHtml(item.className)}">
            <i>${escapeHtml(item.label || `依据 ${index + 1}`)}</i>
            <strong>${escapeHtml(item.title || "回答依据")}</strong>
            <em>${escapeHtml(item.summary || "可结合当前项目复核。")}</em>
            ${item.source ? `<small>${escapeHtml(item.source)}</small>` : ""}
          </span>
        `)
        .join("")}
    </div>
  `;
}

function mergeAssistantAnswerEvidence(result = {}) {
  if (result.intent === "fallback") return [];
  const retrieved = Array.isArray(result.retrieved_context) ? result.retrieved_context : [];
  const legacyEvidence = Array.isArray(result.evidence) ? result.evidence : [];
  const normalized = [
    ...retrieved.map(normalizeRetrievedAssistantEvidence),
    ...legacyEvidence.map(normalizeLegacyAssistantEvidence),
  ].filter((item) => item.title && item.summary);
  const seen = new Set();
  return normalized
    .sort((a, b) => b.priority - a.priority)
    .filter((item) => {
      const key = `${item.type}:${item.title}:${item.summary}`.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 3);
}

function normalizeRetrievedAssistantEvidence(item = {}) {
  const type = item.type || "assistant_knowledge";
  const label = assistantEvidenceTypeLabel(type);
  const title = item.title || label;
  const summary = item.content || item.preview || item.reason || item.category || "";
  const source = item.source || item.category || "";
  return {
    type,
    label,
    title: String(title || "").trim(),
    summary: String(summary || "").trim(),
    source: String(source || "").trim(),
    priority: assistantEvidencePriority(type),
    className: `evidence-${type}`.replace(/[^a-z0-9_-]/gi, "-"),
  };
}

function normalizeLegacyAssistantEvidence(item = {}) {
  const title = item.citation || item.title || item.source_title || "智库依据";
  const summary = item.preview || item.summary || item.reason || "";
  const source = item.source_title || item.source_type_label || item.knowledge_type_label || "";
  return {
    type: "rag_evidence",
    label: "智库依据",
    title: String(title || "").trim(),
    summary: String(summary || "").trim(),
    source: String(source || "").trim(),
    priority: assistantEvidencePriority("rag_evidence") - 0.05,
    className: "evidence-rag_evidence",
  };
}

function assistantEvidenceTypeLabel(type) {
  const labels = {
    project_summary: "项目状态",
    project_overview: "项目概况",
    score_points: "评分点",
    outline_preview: "目录依据",
    generation_status: "正文状态",
    page_advice: "当前步骤",
    assistant_knowledge: "编标经验",
    rag_evidence: "智库依据",
    template_candidate: "投标模板",
    rag_result_count: "智库依据",
    platform_capability: "平台能力",
    bid_intelligence: "标讯情报",
    enterprise_risk: "企业风险",
    supplier_recommendation: "供应商推荐",
    knowledge_base: "投标知识库",
  };
  return labels[type] || "回答依据";
}

function assistantEvidencePriority(type) {
  const priorities = {
    assistant_knowledge: 100,
    rag_evidence: 95,
    knowledge_base: 94,
    score_points: 90,
    project_overview: 85,
    platform_capability: 84,
    project_summary: 80,
    generation_status: 75,
    outline_preview: 70,
    page_advice: 65,
    template_candidate: 60,
    rag_result_count: 55,
    bid_intelligence: 54,
    enterprise_risk: 54,
    supplier_recommendation: 54,
  };
  return priorities[type] || 40;
}

function renderWorkflowSteps(steps) {
  const container = $("#workflow-steps");
  container.innerHTML = "";
  for (const [index, step] of steps.entries()) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `workflow-step ${step.status || "pending"}${step.key === state.activeStep ? " selected" : ""}`;
    item.dataset.step = step.key;
    item.innerHTML = `
      <span class="step-index">${index + 1}</span>
      <span class="step-main">
        <strong>${escapeHtml(step.title)}</strong>
        <small>${escapeHtml(step.hint)}</small>
      </span>
    `;
    item.addEventListener("click", () => setActiveStep(step.key));
    container.appendChild(item);
  }
}

function renderStageBanner(jobs) {
  const banner = $("#stage-banner");
  if (!banner) return;
  const activeJob = (jobs || []).find(isActiveJob);
  const parseDone = state.workflowSummary?.steps?.find((step) => step.key === "parse")?.status === "done";
  if (activeJob) {
    banner.className = "stage-banner running";
    banner.innerHTML = `
      <strong>${escapeHtml(activeJob.job_label || activeJob.job_type || "任务执行中")}</strong>
      <span>${escapeHtml(activeJob.message || activeJob.status_label || "正在处理，请稍候。")}</span>
    `;
    return;
  }
  if (parseDone && state.activeStep === "parse") {
    const scoreCount = state.workflowSummary?.stats?.score_points ?? 0;
    const reviewCount = state.workflowSummary?.stats?.review_items ?? 0;
    const lastJob = (jobs || []).find((job) => job.job_type === "tender_parse" && job.status === "succeeded");
    banner.className = "stage-banner succeeded";
    banner.innerHTML = `
      <div>
        <strong>解析完成</strong>
        <span>招标文件解析报告已生成：识别 ${scoreCount} 个技术标评分点，形成 ${reviewCount} 条复核项。</span>
        ${lastJob?.updated_at ? `<em>完成时间：${escapeHtml(fmtTime(lastJob.updated_at))}</em>` : ""}
      </div>
      <button class="secondary small-button" data-rerun-job="tender_parse" type="button">重新解析</button>
    `;
    banner.querySelector("[data-rerun-job]")?.addEventListener("click", (event) => {
      createJob(event.currentTarget.dataset.rerunJob, event.currentTarget);
    });
    return;
  }
  const stepMessages = {
    outline: {
      title: "目录确认",
      text: "请检查技术标一级目录是否严格采用评分点原文，二三级目录是否符合编标习惯。",
    },
    generate: {
      title: "正文生成",
      text: "可选择少量章节试跑，也可继续未完成章节；已生成内容不会自动重复生成。",
    },
    review: {
      title: "Word 初稿",
      text: "请检查目录页码、正文标题、表格和图片位置；在线修改后记得保存再下载。",
    },
  };
  const message = stepMessages[state.activeStep];
  if (message) {
    banner.className = "stage-banner info";
    banner.innerHTML = `
      <strong>${escapeHtml(message.title)}</strong>
      <span>${escapeHtml(message.text)}</span>
    `;
    return;
  }
  banner.className = "stage-banner hidden";
  banner.innerHTML = "";
}

function updateParseActionState(summary = state.workflowSummary) {
  const button = $("#start-tender-parse");
  if (!button) return;
  const hasTender = Number(summary?.stats?.tender_files || 0) > 0;
  const running = (summary?.latest_jobs || []).some((job) => job.job_type === "tender_parse" && isActiveJob(job));
  const parseDone = workflowStepDone(summary, "parse");
  button.disabled = !hasTender || running;
  button.textContent = running ? "解析中" : parseDone ? "重新解析招标文件" : "启动招标文件解析";
  button.title = hasTender
    ? parseDone
      ? "重新调用模型与规则解析，覆盖当前解析报告和评分点列表。"
      : "解析项目信息、技术评分点和技术标准要求"
    : "请先上传招标文件";
}

function updateWorkflowActionButtons(summary = state.workflowSummary) {
  updateParseActionState(summary);

  const outlineButton = document.querySelector('[data-job="outline_generation"]');
  if (outlineButton) {
    const parseDone = workflowStepDone(summary, "parse");
    const outlineDone = workflowStepDone(summary, "outline");
    const running = (summary?.latest_jobs || []).some((job) => job.job_type === "outline_generation" && isActiveJob(job));
    outlineButton.disabled = !parseDone || running;
    outlineButton.textContent = running ? "目录生成中" : outlineDone ? "重新生成目录" : "生成目录";
    outlineButton.title = !parseDone
      ? "请先完成招标文件解析。"
      : outlineDone
        ? "重新生成会覆盖当前目录预览，用户手动调整后请谨慎操作。"
        : "根据评分点生成技术标目录。";
  }

  const chapterInputButton = document.querySelector('[data-job="chapter_generation"]');
  if (chapterInputButton) {
    const outlineDone = workflowStepDone(summary, "outline");
    const inputDone = workflowStepDone(summary, "generate");
    const running = (summary?.latest_jobs || []).some((job) => job.job_type === "chapter_generation" && isActiveJob(job));
    chapterInputButton.disabled = !outlineDone || running;
    chapterInputButton.textContent = running ? "输入包刷新中" : inputDone ? "重新刷新输入包" : "刷新输入包（不调用大模型）";
    chapterInputButton.title = !outlineDone
      ? "请先生成并确认目录。"
      : "只刷新正文生成输入包，不调用大模型。";
  }
}

function renderScorePoints(items) {
  const list = $("#score-points-list");
  list.innerHTML = "";
  for (const [index, item] of items.entries()) {
    const confirmed = isScorePointConfirmed(item, index);
    const row = document.createElement("div");
    row.className = `score-item${confirmed ? " confirmed" : ""}`;
    row.innerHTML = `
      <span class="score-index">${index + 1}</span>
      <div class="score-main">
        <div class="score-title-row">
          <strong>${escapeHtml(item.title)}</strong>
          <span class="status-pill ${confirmed ? "ok" : "pending"}">${confirmed ? "已确认" : "待确认"}</span>
          <button type="button" class="${confirmed ? "secondary" : "small-button"} score-confirm-button" data-confirm-score="${index}">
            ${confirmed ? "已确认" : "确认"}
          </button>
        </div>
      </div>
    `;
    list.appendChild(row);
  }
  list.querySelectorAll("[data-confirm-score]").forEach((button) => {
    button.addEventListener("click", () => setScorePointConfirmed(Number(button.dataset.confirmScore), true));
  });
  if (!items.length) {
    list.innerHTML = '<div class="mini-empty">启动招标文件解析后，这里展示技术标评分点原文。</div>';
  }
}

function renderParseReviewPanel() {
  const container = $("#parse-review-checklist");
  if (!container) return;
  const items = state.workflowSummary?.score_points || [];
  const summary = state.workflowSummary?.parse_review_summary || {};
  const total = items.length;
  const confirmedCount = items.filter((item, index) => isScorePointConfirmed(item, index)).length;
  const pendingCount = Math.max(0, total - confirmedCount);
  container.innerHTML = `
    <div class="parse-review-summary">
      <div>
        <strong>${total ? `${pendingCount} 项待确认` : "等待解析结果"}</strong>
        <small>${total ? `已确认 ${confirmedCount} / ${total} 个技术标评分点` : "解析完成后，可在这里确认评分点是否可作为一级目录。"}</small>
      </div>
      <button type="button" class="secondary small-button" id="confirm-all-score-points" ${total ? "" : "disabled"}>全部确认评分点</button>
    </div>
    ${renderSummaryCard("项目信息", summary.project_info || [])}
    ${renderSummaryCard("技术要求信息", summary.technical_requirements || [])}
    ${renderAttentionItems(summary.attention_items || [])}
  `;
  $("#confirm-all-score-points")?.addEventListener("click", confirmAllScorePoints);
}

function renderSummaryCard(title, rows) {
  const content = rows.length
    ? rows.map((row) => `
        <div class="summary-row">
          <span>${escapeHtml(row.label)}</span>
          <strong>${escapeHtml(row.value || "未明确")}</strong>
        </div>
      `).join("")
    : '<div class="mini-empty">解析完成后展示摘要。</div>';
  return `
    <section class="summary-card">
      <h3>${escapeHtml(title)}</h3>
      <div class="summary-rows">${content}</div>
    </section>
  `;
}

function renderAttentionItems(items) {
  if (!items.length) return "";
  return `
    <section class="summary-card compact-summary">
      <h3>待关注</h3>
      <ul class="attention-list">
        ${items.slice(0, 3).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </section>
  `;
}

function renderOutline(nodes) {
  const tree = $("#outline-preview");
  tree.innerHTML = "";
  if (!nodes.length) {
    tree.innerHTML = '<div class="mini-empty">目录生成后展示一级评分点与二三级目录。</div>';
    return;
  }
  tree.innerHTML = nodes.map((node, index) => renderOutlineNode(node, [index], 1)).join("");
}

function renderOutlineNode(node, path, level) {
  const normalized = normalizeOutlineNode(node, path, level);
  const key = outlinePathKey(path);
  const hasChildren = normalized.children.length > 0;
  const collapsed = state.outlineCollapsed.has(key);
  const childHtml = hasChildren && !collapsed
    ? `<div class="outline-children">${normalized.children.map((child, index) => renderOutlineNode(child, [...path, index], level + 1)).join("")}</div>`
    : "";
  const canAdd = level < 3;
  const canEdit = level > 1;
  const canDelete = level > 1;
  const titleSuffix = normalized.domain === "design" && level === 1 ? "（设计）" : "";
  return `
    <div class="outline-node level-${level}" data-outline-path="${escapeHtml(key)}">
      <div class="outline-node-row">
        <button type="button" class="outline-toggle" data-outline-action="toggle" data-outline-path="${escapeHtml(key)}" ${hasChildren ? "" : "disabled"} title="${collapsed ? "展开" : "折叠"}">
          ${hasChildren ? (collapsed ? "＋" : "－") : ""}
        </button>
        <span class="outline-number">${escapeHtml(normalized.number)}</span>
        <span class="outline-title">${escapeHtml(normalized.title)}${titleSuffix}</span>
        <span class="outline-actions">
          ${canAdd ? `<button type="button" class="secondary small-button" data-outline-action="add" data-outline-path="${escapeHtml(key)}">添加子节点</button>` : ""}
          ${canEdit ? `<button type="button" class="secondary small-button" data-outline-action="edit" data-outline-path="${escapeHtml(key)}">编辑</button>` : ""}
          ${canDelete ? `<button type="button" class="danger-button small-button" data-outline-action="delete" data-outline-path="${escapeHtml(key)}">删除</button>` : ""}
        </span>
      </div>
      ${childHtml}
    </div>
  `;
}

function normalizeOutlineNode(node, path, level) {
  const safeNode = typeof node === "string" ? { title: node } : (node || {});
  return {
    ...safeNode,
    title: String(safeNode.title || "未命名目录").trim(),
    number: safeNode.number || path.map((item) => item + 1).join("."),
    domain: safeNode.domain,
    children: Array.isArray(safeNode.children) ? safeNode.children : [],
    level,
  };
}

function outlinePathKey(path) {
  return path.join(".");
}

function parseOutlinePath(pathKey) {
  return String(pathKey || "")
    .split(".")
    .filter((part) => part !== "")
    .map((part) => Number(part));
}

function outlineNodes() {
  if (!state.workflowSummary) return [];
  if (!Array.isArray(state.workflowSummary.outline_preview)) {
    state.workflowSummary.outline_preview = [];
  }
  return state.workflowSummary.outline_preview;
}

function getOutlineNode(path) {
  let nodes = outlineNodes();
  let node = null;
  for (const index of path) {
    node = nodes[index];
    if (!node) return null;
    if (!Array.isArray(node.children)) node.children = [];
    nodes = node.children;
  }
  return node;
}

function renumberOutline(nodes = outlineNodes(), prefix = "") {
  nodes.forEach((node, index) => {
    if (!node || typeof node === "string") return;
    node.number = prefix ? `${prefix}.${index + 1}` : `${index + 1}`;
    if (Array.isArray(node.children)) {
      renumberOutline(node.children, node.number);
    }
  });
}

function editOutlineNode(pathKey) {
  const path = parseOutlinePath(pathKey);
  if (path.length <= 1) {
    toast("一级目录来自评分点原文，不能编辑");
    return;
  }
  const node = getOutlineNode(path);
  if (!node) return;
  const nextTitle = window.prompt("请输入目录名称", node.title || "");
  if (nextTitle === null) return;
  const trimmed = nextTitle.trim();
  if (!trimmed) {
    toast("目录名称不能为空");
    return;
  }
  node.title = trimmed;
  renumberOutline();
  renderOutline(outlineNodes());
}

function addOutlineChild(pathKey) {
  const path = parseOutlinePath(pathKey);
  if (path.length >= 3) {
    toast("三级目录下暂不继续添加子节点");
    return;
  }
  const node = getOutlineNode(path);
  if (!node) return;
  const title = window.prompt("请输入子目录名称");
  if (title === null) return;
  const trimmed = title.trim();
  if (!trimmed) {
    toast("目录名称不能为空");
    return;
  }
  if (!Array.isArray(node.children)) node.children = [];
  node.children.push({ title: trimmed, children: [] });
  state.outlineCollapsed.delete(pathKey);
  renumberOutline();
  renderOutline(outlineNodes());
}

function deleteOutlineNode(pathKey) {
  const path = parseOutlinePath(pathKey);
  if (path.length <= 1) {
    toast("一级目录来自评分点原文，不能删除");
    return;
  }
  const parent = getOutlineNode(path.slice(0, -1));
  if (!parent || !Array.isArray(parent.children)) return;
  const node = parent.children[path[path.length - 1]];
  if (!node) return;
  if (!window.confirm(`确认删除目录：${node.title || "未命名目录"}？`)) return;
  parent.children.splice(path[path.length - 1], 1);
  renumberOutline();
  renderOutline(outlineNodes());
}

function toggleOutlineNode(pathKey) {
  if (state.outlineCollapsed.has(pathKey)) {
    state.outlineCollapsed.delete(pathKey);
  } else {
    state.outlineCollapsed.add(pathKey);
  }
  renderOutline(outlineNodes());
}

function collectOutlineKeys(nodes = outlineNodes(), prefix = []) {
  const keys = [];
  nodes.forEach((node, index) => {
    const path = [...prefix, index];
    const children = Array.isArray(node?.children) ? node.children : [];
    if (children.length) {
      keys.push(outlinePathKey(path));
      keys.push(...collectOutlineKeys(children, path));
    }
  });
  return keys;
}

function setOutlineCollapsedAll(collapsed) {
  state.outlineCollapsed = collapsed ? new Set(collectOutlineKeys()) : new Set();
  renderOutline(outlineNodes());
}

function handleOutlineAction(event) {
  const button = event.target.closest("[data-outline-action]");
  if (!button) return;
  const action = button.dataset.outlineAction;
  const pathKey = button.dataset.outlinePath;
  if (action === "toggle") toggleOutlineNode(pathKey);
  if (action === "edit") editOutlineNode(pathKey);
  if (action === "add") addOutlineChild(pathKey);
  if (action === "delete") deleteOutlineNode(pathKey);
}

async function saveOutlineAdjustments() {
  if (!state.selectedProjectId) {
    toast("请先选择项目");
    return;
  }
  const nodes = outlineNodes();
  if (!nodes.length) {
    toast("暂无目录可保存");
    return;
  }
  renumberOutline(nodes);
  try {
    const result = await api(`/api/v1/projects/${encodeURIComponent(state.selectedProjectId)}/outline`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nodes }),
    });
    state.workflowSummary.outline_preview = result.outline_preview || nodes;
    state.artifactPreviewCache = {};
    renderOutline(state.workflowSummary.outline_preview);
    renderArtifactDownloadButtons(state.workflowSummary.artifacts || {});
    toast("目录调整已保存");
  } catch (error) {
    toast(`目录保存失败：${error.message}`);
  }
}

function renderGenerationUnits(items) {
  const list = $("#generation-units");
  list.innerHTML = "";
  renderGenerationProgressSummary(items);
  renderGenerationCurrentJob(state.workflowSummary?.latest_jobs || []);
  if (!items.length) {
    list.innerHTML = '<div class="mini-empty">确认目录后，按小节包展示正文任务。</div>';
    renderGenerationSelectionSummary(items);
    updateGenerationActionState(items);
    return;
  }
  const validIds = new Set(items.map((item) => String(item.unit_id || "")).filter(Boolean));
  state.selectedGenerationUnits = new Set(
    [...state.selectedGenerationUnits].filter((unitId) => {
      if (!validIds.has(unitId)) return false;
      const item = items.find((candidate) => String(candidate.unit_id || "") === unitId);
      return item && !isGeneratedGenerationUnit(item);
    })
  );
  for (const item of items) {
    const unitId = String(item.unit_id || "");
    const checked = unitId && state.selectedGenerationUnits.has(unitId);
    const generated = isGeneratedGenerationUnit(item);
    const disabled = !unitId || generated;
    const meta = generationUnitMeta(item);
    const path = Array.isArray(item.chapter_path) ? item.chapter_path.filter(Boolean) : [];
    const title = generationUnitTitle(item);
    const row = document.createElement("div");
    row.className = `chapter-item selectable generation-unit ${checked ? " selected" : ""}${generated ? " generated" : ""}`;
    row.innerHTML = `
      <label class="chapter-select-row">
        <input type="checkbox" data-generation-unit-id="${escapeHtml(unitId)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
        <span>
          <strong>${escapeHtml(title)}</strong>
          <small>${escapeHtml(path.join(" > ") || "未形成章节路径")}</small>
        </span>
      </label>
      <div class="chapter-meta-row">
        <span class="${generationStatusClass(item.status)}">${escapeHtml(generationStatusLabel(item))}</span>
        ${meta.map((text) => `<small>${escapeHtml(text)}</small>`).join("")}
      </div>
    `;
    row.addEventListener("click", (event) => {
      if (event.target.closest("input")) return;
      const checkbox = row.querySelector("[data-generation-unit-id]");
      if (!checkbox || checkbox.disabled) return;
      checkbox.checked = !checkbox.checked;
      updateGenerationUnitSelection(unitId, checkbox.checked);
    });
    list.appendChild(row);
  }
  list.querySelectorAll("[data-generation-unit-id]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => updateGenerationUnitSelection(checkbox.dataset.generationUnitId, checkbox.checked));
  });
  renderGenerationSelectionSummary(items);
  updateGenerationActionState(items);
}

function generationUnitTitle(item) {
  const chapter = String(item?.chapter || "").trim();
  if (chapter && chapter !== "未命名章节") return chapter;
  const path = Array.isArray(item?.chapter_path) ? item.chapter_path.filter(Boolean) : [];
  return String(path[path.length - 1] || "未命名章节");
}

function generationStatusClass(status) {
  const text = String(status || "");
  if (text.includes("失败")) return "generation-status failed";
  if (text.includes("已生成") || text.includes("已完成")) return "generation-status done";
  if (text.includes("本次") || text.includes("生成中")) return "generation-status pending";
  return "generation-status idle";
}

function generationStatusLabel(item) {
  const status = String(item?.status || "");
  if (status) return status;
  if (String(item?.preview_status || "").includes("轻量预览")) return "待生成";
  return "待生成";
}

function isGeneratedGenerationUnit(item) {
  const status = String(item?.status || "");
  return status.includes("已生成") || status.includes("已完成");
}

function isFailedGenerationUnit(item) {
  return String(item?.status || "").includes("失败");
}

function isPendingGenerationUnit(item) {
  if (!item?.unit_id) return false;
  if (isGeneratedGenerationUnit(item)) return false;
  if (isFailedGenerationUnit(item)) return false;
  return true;
}

function isUnfinishedGenerationUnit(item) {
  if (!item?.unit_id) return false;
  return !isGeneratedGenerationUnit(item);
}

function generationUnitMeta(item) {
  const meta = [];
  const cacheStatus = String(item.cache_status || "");
  const duration = fmtDurationSeconds(item.duration_seconds);
  const issueCount = Number(item.validation_issue_count || 0);
  const repairCount = Number(item.repair_attempt_count || 0);
  const retryCount = Number(item.retry_attempt_count || 0);
  const failure = item.failure_reason || item.error || item.failure_type;
  if (cacheStatus === "hit") meta.push("缓存命中");
  if (cacheStatus === "miss") meta.push("缓存未命中");
  if (duration) meta.push(`耗时 ${duration}`);
  if (issueCount > 0) meta.push(`校验提示 ${issueCount} 条`);
  if (retryCount > 0) meta.push(`自动重试 ${retryCount} 次`);
  if (repairCount > 0) meta.push(`JSON 修复 ${repairCount} 次`);
  if (failure && isFailedGenerationUnit(item)) meta.push(`失败原因：${String(failure).slice(0, 40)}`);
  const packageIds = Array.isArray(item.package_unit_ids) ? item.package_unit_ids : [];
  if (packageIds.length > 1) meta.push(`包含 ${packageIds.length} 个小节包`);
  if (!meta.length) meta.push(item.material || "等待正文生成");
  return meta;
}

function generationStats(items = state.workflowSummary?.generation_units || []) {
  const total = items.length;
  const generated = items.filter(isGeneratedGenerationUnit).length;
  const failed = items.filter(isFailedGenerationUnit).length;
  const unfinished = items.filter(isUnfinishedGenerationUnit).length;
  const pending = items.filter(isPendingGenerationUnit).length;
  const cacheHit = items.filter((item) => item.cache_status === "hit").length;
  const previewed = items.filter((item) => String(item.preview_status || "").includes("轻量预览")).length;
  const selected = [...state.selectedGenerationUnits].filter((unitId) => items.some((item) => String(item.unit_id || "") === unitId)).length;
  return { total, generated, failed, unfinished, pending, cacheHit, previewed, selected };
}

function renderGenerationProgressSummary(items = state.workflowSummary?.generation_units || []) {
  const container = $("#generation-progress-summary");
  if (!container) return;
  const stats = generationStats(items);
  container.innerHTML = `
    <span><i>总数</i><strong>${fmtNumber(stats.total)}</strong></span>
    <span><i>已生成</i><strong>${fmtNumber(stats.generated)}</strong></span>
    <span><i>待生成</i><strong>${fmtNumber(stats.pending)}</strong></span>
    <span><i>失败</i><strong>${fmtNumber(stats.failed)}</strong></span>
    <span><i>缓存命中</i><strong>${fmtNumber(stats.cacheHit)}</strong></span>
  `;
}

function updateGenerationUnitSelection(unitId, checked) {
  if (!unitId) return;
  const item = (state.workflowSummary?.generation_units || []).find((candidate) => String(candidate.unit_id || "") === unitId);
  if (checked && item && isGeneratedGenerationUnit(item)) {
    toast("已生成小节包默认不重跑，如需重写请先清除章节状态或使用后续强制重跑能力。");
    return;
  }
  if (checked) {
    state.selectedGenerationUnits.add(unitId);
  } else {
    state.selectedGenerationUnits.delete(unitId);
  }
  renderGenerationUnits(state.workflowSummary?.generation_units || []);
  refreshAiContext({ silent: true });
}

function renderGenerationSelectionSummary(items = state.workflowSummary?.generation_units || []) {
  const summary = $("#generation-selection-summary");
  if (!summary) return;
  const stats = generationStats(items);
  summary.textContent = `已选 ${stats.selected} / ${stats.total} 个小节包，已生成 ${stats.generated} 个，失败 ${stats.failed} 个${stats.previewed ? `，已准备输入包 ${stats.previewed} 个` : ""}。`;
}

function selectGenerationUnitsByFilter(filter) {
  const items = state.workflowSummary?.generation_units || [];
  state.selectedGenerationUnits = new Set(
    items
      .filter(filter)
      .map((item) => String(item.unit_id || ""))
      .filter(Boolean)
  );
  renderGenerationUnits(items);
  refreshAiContext({ silent: true });
}

function selectPendingGenerationUnits() {
  selectGenerationUnitsByFilter(isUnfinishedGenerationUnit);
}

function selectFailedGenerationUnits() {
  selectGenerationUnitsByFilter(isFailedGenerationUnit);
}

function clearGenerationUnits() {
  state.selectedGenerationUnits = new Set();
  renderGenerationUnits(state.workflowSummary?.generation_units || []);
  refreshAiContext({ silent: true });
}

function updateGenerationActionState(items = state.workflowSummary?.generation_units || []) {
  const stats = generationStats(items);
  const activeJob = latestChapterGenerationJob();
  const generating = Boolean(activeJob);
  const outlineDone = workflowStepDone(state.workflowSummary, "outline");
  const selectedButton = $("#generate-selected-chapters");
  const continueButton = $("#continue-unfinished-chapters");
  const retryButton = $("#retry-failed-chapters");
  if (selectedButton) {
    selectedButton.disabled = !outlineDone || generating || stats.selected === 0;
    selectedButton.textContent = generating ? "正文生成中" : "生成选中章节（调用大模型）";
  }
  if (continueButton) {
    continueButton.disabled = !outlineDone || generating || stats.unfinished === 0;
    continueButton.textContent = generating ? "等待生成完成" : "继续未完成（调用大模型）";
  }
  if (retryButton) {
    retryButton.disabled = !outlineDone || generating || stats.failed === 0;
    retryButton.textContent = generating ? "等待生成完成" : "重试失败（调用大模型）";
  }
}

function latestChapterGenerationJob() {
  const jobs = state.workflowSummary?.latest_jobs || [];
  return jobs.find((job) => job.job_type === "chapter_llm_generation" && isActiveJob(job));
}

function renderGenerationCurrentJob(jobs = state.workflowSummary?.latest_jobs || []) {
  const container = $("#generation-current-job");
  if (!container) return;
  const job = (jobs || []).find((item) => item.job_type === "chapter_llm_generation" && isActiveJob(item));
  if (!job) {
    container.innerHTML = '<span>当前没有正文生成任务在运行。</span>';
    return;
  }
  const percent = Math.max(0, Math.min(99, Number(job.progress_percent || 0)));
  const total = Number(job.progress_total || 0);
  const completed = Number(job.progress_completed || 0);
  const failed = Number(job.progress_failed || 0);
  const retrying = Number(job.metadata?.retrying || 0);
  const currentPath = Array.isArray(job.metadata?.chapter_path) ? job.metadata.chapter_path.join(" > ") : "";
  const showCountProgress = job.job_type === "chapter_llm_generation";
  const progressLabel = showCountProgress ? "小节包" : "进度";
  container.innerHTML = `
    <div class="generation-current-head">
      <strong>正在生成正文</strong>
      <span>${percent.toFixed(percent % 1 ? 1 : 0)}%</span>
    </div>
    <div class="progress-bar"><i style="width: ${percent}%"></i></div>
    <div class="generation-current-meta">
      ${showCountProgress && total ? `<span>${escapeHtml(progressLabel)} ${fmtNumber(completed)} / ${fmtNumber(total)}</span>` : ""}
      ${failed ? `<span>失败 ${fmtNumber(failed)}</span>` : ""}
      ${retrying ? `<span>自动重试中 ${fmtNumber(retrying)}</span>` : ""}
      ${job.metadata?.max_workers ? `<span>并发 ${escapeHtml(job.metadata.max_workers)}</span>` : ""}
      ${currentPath ? `<span>刚处理：${escapeHtml(currentPath)}</span>` : ""}
    </div>
    <small>${escapeHtml(job.message || "正在调用大模型生成章节正文。")}</small>
  `;
}

function renderGenerationResultActions(artifacts) {
  const container = $("#generation-result-actions");
  if (!container) return;
  const entries = [
    ["llm_draft_markdown", "查看正文预览", "preview"],
    ["word_draft_docx", "下载 Word 初稿", "download"],
  ];
  const html = entries
    .map(([key, label, action]) => {
      const item = artifacts[key];
      if (!item) return "";
      const downloadUrl = item.download_url || (state.selectedProjectId ? artifactDownloadUrl(state.selectedProjectId, key) : "");
      if (action === "preview" && item.previewable) {
        return `<button type="button" class="secondary small-button" data-preview-artifact="${escapeHtml(key)}" data-preview-target="#chapter-preview">${escapeHtml(label)}</button>`;
      }
      if (action === "download" && downloadUrl) {
        return `<a class="download-link" href="${escapeHtml(downloadUrl)}" download>${escapeHtml(label)}</a>`;
      }
      return `
        ${downloadUrl ? `<a class="download-link" href="${escapeHtml(downloadUrl)}" download>${escapeHtml(label)}</a>` : ""}
      `;
    })
    .join("");
  container.innerHTML = html || '<span class="muted">生成后可在这里查看正文预览，并下载 Word 初稿。</span>';
  container.querySelectorAll("[data-preview-artifact]").forEach((button) => {
    button.addEventListener("click", () => loadArtifactPreview(button.dataset.previewArtifact, button.dataset.previewTarget, { compact: true }));
  });
}

async function refreshWordDraft(button = null) {
  if (!state.selectedProjectId) {
    toast("请先选择项目");
    return null;
  }
  const originalText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "刷新中";
  }
  try {
    const job = await api(`/api/v1/projects/${state.selectedProjectId}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_type: "chapter_aggregate_refresh",
        message: "刷新正文聚合结果和 Word 初稿，不调用大模型。",
      }),
    });
    state.artifactPreviewCache = {};
    state.activeStepPinned = false;
    toast("已开始刷新 Word 初稿，不调用大模型");
    await refreshProjectSnapshot({ silent: true });
    return job;
  } catch (error) {
    toast(`刷新 Word 初稿失败：${error.message}`);
    return null;
  } finally {
    if (button) {
      button.disabled = false;
      updateGenerationActionState();
      button.textContent = originalText;
    }
  }
}

async function refreshWordExportPage(options = {}) {
  if (!state.selectedProjectId) return;
  const includeOnlyOffice = options.includeOnlyOffice !== false;
  try {
    const [summary, profile] = await Promise.all([
      api(`/api/v1/projects/${state.selectedProjectId}/word/summary`),
      api(`/api/v1/projects/${state.selectedProjectId}/word/export-profile`),
    ]);
    state.wordSummary = summary;
    state.wordProfile = profile;
    renderWordExportPage();
    if (includeOnlyOffice) {
      await loadOnlyOfficePreview({ silent: true });
    }
  } catch (error) {
    renderWordExportFallback(`成稿状态读取失败：${error.message}`);
  }
}

function setWordReviewDrawer(open) {
  const drawer = $("#word-review-drawer");
  const backdrop = $("#word-drawer-backdrop");
  const workspace = $("#project-workspace");
  if (drawer) {
    drawer.classList.toggle("open", open);
    drawer.setAttribute("aria-hidden", open ? "false" : "true");
  }
  if (backdrop) {
    backdrop.hidden = !open;
  }
  if (workspace) {
    workspace.classList.toggle("review-drawer-open", open);
  }
}

function renderWordExportPage() {
  renderWordSummary();
  renderWordVersionFiles();
  renderWordQualitySummary();
  renderWordProfileForm();
}

function renderWordSummary() {
  const summary = state.wordSummary;
  const stats = summary?.stats || {};
  const statusText = $("#word-status-text");
  if (!statusText) return;
  const latest = summary?.latest_version ? fmtWordVersionName(summary.latest_version) : "暂无可下载版本";
  statusText.textContent = summary
    ? `${fmtWordStatus(summary.word_status)} · 当前版本：${latest} · 更新时间：${fmtTime(summary.generated_at)}`
    : "进入成稿导出页后读取 Word 成稿状态。";
  $("#word-stat-paragraphs").textContent = fmtNumber(stats.paragraph_count);
  $("#word-stat-tables").textContent = fmtNumber(stats.table_count);
  $("#word-stat-images").textContent = fmtNumber(stats.image_count);
  $("#word-stat-headings").textContent = fmtNumber(stats.heading_count);
  const download = $("#word-download-latest");
  if (download) {
    if (summary?.download_url && summary.word_status !== "missing") {
      download.href = summary.download_url;
      download.setAttribute("download", "");
      download.classList.remove("disabled");
      download.textContent = summary?.files?.review_editing?.exists ? "下载 Word 复核稿" : "下载 Word 初稿";
    } else {
      download.removeAttribute("href");
      download.removeAttribute("download");
      download.classList.add("disabled");
      download.textContent = "暂无 Word";
    }
  }
}

function renderWordQualitySummary() {
  const container = $("#word-quality-summary");
  if (!container) return;
  const summary = state.wordSummary;
  if (!summary) {
    container.innerHTML = '<div class="mini-empty">正在读取成稿质量摘要。</div>';
    return;
  }
  const stats = summary.stats || {};
  const toc = summary.toc_status || {};
  const consistency = summary.outline_consistency || {};
  const tips = summary.review_tips || [];
  const warnings = consistency.warnings || [];
  container.innerHTML = `
    <div class="quality-grid">
      ${renderQualityMetric("一级标题", consistency.level1_count, "目录一级节点")}
      ${renderQualityMetric("二级标题", consistency.level2_count, "目录二级节点")}
      ${renderQualityMetric("三级标题", consistency.level3_count, "目录三级节点")}
      ${renderQualityMetric("空标题", consistency.empty_heading_count, "应为 0")}
      ${renderQualityMetric("缺失图片", stats.missing_image_count, "应为 0")}
      ${renderQualityMetric("占位内容", stats.placeholder_count, "建议复核")}
    </div>
    <div class="quality-card ${consistency.heading_count_matched ? "ok" : "warn"}">
      <strong>目录与正文标题一致性</strong>
      <span>${consistency.heading_count_matched ? "未发现明显不一致" : "存在标题数量差异"}</span>
      <small>预计 ${fmtNumber(consistency.expected_heading_count)} 个，Word 中 ${fmtNumber(consistency.actual_heading_count)} 个。</small>
    </div>
    <div class="quality-card ${toc.inserted ? "ok" : "warn"}">
      <strong>目录与页码</strong>
      <span>${toc.inserted ? "已插入 Word 原生目录字段" : "尚未检测到目录字段"}</span>
      <small>目录级数 ${toc.levels || 3}；正文页码从 ${toc.body_page_restart_at || toc.body_page_number_start || 1} 开始。点击“目录页码”查看更新方式。</small>
    </div>
    ${renderQualityList("质量提示", [...warnings, ...tips])}
  `;
}

function renderQualityMetric(label, value, hint) {
  return `
    <span>
      <i>${escapeHtml(label)}</i>
      <strong>${fmtNumber(value)}</strong>
      <em>${escapeHtml(hint || "")}</em>
    </span>
  `;
}

function renderQualityList(title, items) {
  if (!items.length) {
    return `
      <div class="quality-card ok">
        <strong>${escapeHtml(title)}</strong>
        <span>暂无严重提示。</span>
      </div>
    `;
  }
  return `
    <div class="quality-card warn">
      <strong>${escapeHtml(title)}</strong>
      <ul>${items.slice(0, 8).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    </div>
  `;
}

function renderWordProfileForm() {
  const form = $("#word-profile-form");
  if (!form || !state.wordProfile) return;
  form.querySelectorAll("input[name], select[name]").forEach((input) => {
    const value = getNestedValue(state.wordProfile, input.name);
    if (input.type === "checkbox") {
      input.checked = Boolean(value);
    } else {
      input.value = value ?? "";
    }
  });
}

function collectWordProfileForm() {
  const form = $("#word-profile-form");
  const base = structuredClone(state.wordProfile || {});
  form?.querySelectorAll("input[name], select[name]").forEach((input) => {
    if (input.type === "checkbox") {
      setNestedValue(base, input.name, input.checked);
      return;
    }
    if (input.value === "") return;
    const value = input.type === "number" ? Number(input.value) : input.value;
    setNestedValue(base, input.name, value);
  });
  return base;
}

function renderWordVersionFiles() {
  const container = $("#word-version-files");
  if (!container) return;
  const summary = state.wordSummary;
  if (!summary) {
    container.innerHTML = '<div class="mini-empty">正在读取版本文件。</div>';
    return;
  }
  const files = summary.files || {};
  const rows = ["system_generated", "review_editing", "final_export"]
    .map((key) => {
      const file = files[key] || {};
      const exists = Boolean(file.exists);
      return `
        <article class="word-version-card ${exists ? "ready" : "missing"}">
          <div>
            <strong>${escapeHtml(fmtWordVersionName(key))}</strong>
            <small>${exists ? `${escapeHtml(file.file_name || "")} · ${fmtNumber(file.size)} bytes · ${fmtTime(file.modified_at)}` : "尚未生成"}</small>
          </div>
          ${exists && file.download_url ? `<a class="download-link" href="${escapeHtml(file.download_url)}" download>下载</a>` : ""}
        </article>
      `;
    })
    .join("");
  container.innerHTML = rows;
}

async function saveWordProfile(event) {
  event.preventDefault();
  if (!state.selectedProjectId) return;
  const button = event.submitter;
  const originalText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "保存中";
  }
  try {
    const profile = collectWordProfileForm();
    state.wordProfile = await api(`/api/v1/projects/${state.selectedProjectId}/word/export-profile`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile }),
    });
    renderWordProfileForm();
    toast("格式设置已保存");
  } catch (error) {
    toast(`格式保存失败：${error.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      updateWorkflowActionButtons(state.workflowSummary);
      if (!state.workflowSummary) button.textContent = originalText;
    }
  }
}

async function saveOnlyOfficeDocument(button = null) {
  if (!state.selectedProjectId) return;
  const originalText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "保存中";
  }
  try {
    const result = await api(`/api/v1/projects/${state.selectedProjectId}/word/onlyoffice-force-save`, {
      method: "POST",
    });
    if (!result?.saved) {
      toast("已发送保存命令，但复核稿尚未写回，请稍后再点保存或刷新状态");
      return;
    }
    await refreshWordExportPage({ includeOnlyOffice: false });
    toast("已保存在线修改，下载 Word 将使用最新复核稿");
  } catch (error) {
    toast(`保存失败：${error.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
  }
}

async function resetWordProfile(button = null) {
  if (!state.selectedProjectId) return;
  const originalText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "恢复中";
  }
  try {
    state.wordProfile = await api(`/api/v1/projects/${state.selectedProjectId}/word/export-profile/reset`, { method: "POST" });
    renderWordProfileForm();
    toast("已恢复默认格式");
  } catch (error) {
    toast(`恢复默认格式失败：${error.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
  }
}

async function reexportWord(button = null) {
  if (!state.selectedProjectId) {
    toast("请先选择项目");
    return;
  }
  const originalText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "导出中";
  }
  try {
    const profile = collectWordProfileForm();
    const result = await api(`/api/v1/projects/${state.selectedProjectId}/word/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile, save_profile: true, force: true }),
    });
    state.wordSummary = result.summary || null;
    state.wordProfile = profile;
    renderWordExportPage();
    toast("Word 成稿已重新导出，未调用大模型");
    await loadOnlyOfficePreview({ silent: true });
    await refreshProjectSnapshot({ silent: true });
  } catch (error) {
    toast(`重新导出失败：${error.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
  }
}

async function loadOnlyOfficePreview(options = {}) {
  const container = $("#onlyoffice-preview");
  if (!container || !state.selectedProjectId) return;
  if (!options.silent) {
    container.innerHTML = '<div class="mini-empty">正在加载 OnlyOffice 预览。</div>';
  }
  try {
    const config = await api(`/api/v1/projects/${state.selectedProjectId}/word/onlyoffice-config`);
    state.onlyOfficeConfig = config;
    renderOnlyOfficePreview(config);
  } catch (error) {
    renderOnlyOfficeFallback(error.message);
  }
}

async function openTocPageNumberHelp(button = null) {
  const originalText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "正在打开";
  }
  try {
    await loadOnlyOfficePreview({ silent: false });
    toast("已打开 Word 预览。请在目录页点击目录区域，选择更新目录或更新页码；导出前再保存。");
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
  }
}

async function fullscreenOnlyOfficePreview(button = null) {
  const container = $("#onlyoffice-preview");
  if (!container) return;
  try {
    if (!document.fullscreenElement) {
      await container.requestFullscreen?.();
      setTimeout(() => window.dispatchEvent(new Event("resize")), 300);
      toast("已进入全屏预览。退出全屏后文档仍会保留。");
    } else {
      await document.exitFullscreen?.();
    }
  } catch (error) {
    toast(`全屏预览失败：${error.message}`);
  }
}

function renderOnlyOfficePreview(config) {
  const container = $("#onlyoffice-preview");
  if (!container) return;
  const serverUrl = String(config?.document_server_url || "").replace(/\/$/, "");
  if (!serverUrl) {
    renderOnlyOfficeFallback("OnlyOffice 服务地址尚未配置或不可访问。");
    return;
  }
  const previewHeight = getOnlyOfficePreviewHeight(container);
  const previewWidth = getOnlyOfficePreviewWidth(container);
  const editorConfig = config.editor_config || {};
  const editorConfigWithDefaults = {
    ...editorConfig,
    width: `${previewWidth}px`,
    height: `${previewHeight}px`,
    editorConfig: {
      ...(editorConfig.editorConfig || {}),
      user: {
        id: "internal-reviewer",
        name: "编标人员",
        ...((editorConfig.editorConfig || {}).user || {}),
      },
    },
    events: {
      ...(editorConfig.events || {}),
      onAppReady: () => {
        requestOnlyOfficeResize();
      },
      onDocumentReady: () => {
        requestOnlyOfficeResize();
      },
    },
  };
  const scriptId = "onlyoffice-api-script";
  container.innerHTML = '<div id="onlyoffice-editor" class="onlyoffice-editor"></div>';
  const editorElement = $("#onlyoffice-editor");
  if (editorElement) {
    editorElement.style.width = `${previewWidth}px`;
    editorElement.style.height = `${previewHeight}px`;
  }
  const createEditor = () => {
    if (!window.DocsAPI?.DocEditor) {
      renderOnlyOfficeFallback("OnlyOffice 前端脚本加载失败。");
      return;
    }
    try {
      state.onlyOfficeEditor?.destroyEditor?.();
      state.onlyOfficeEditor = new window.DocsAPI.DocEditor("onlyoffice-editor", {
        ...editorConfigWithDefaults,
        token: config.token || undefined,
      });
    } catch (error) {
      renderOnlyOfficeFallback(`OnlyOffice 打开失败：${error.message}`);
    }
  };
  const existingScript = document.getElementById(scriptId);
  if (existingScript) {
    createEditor();
    return;
  }
  const script = document.createElement("script");
  script.id = scriptId;
  script.src = `${serverUrl}/web-apps/apps/api/documents/api.js`;
  script.onload = createEditor;
  script.onerror = () => renderOnlyOfficeFallback("OnlyOffice 服务未启动，已保留下载入口。");
  document.body.appendChild(script);
}

function getOnlyOfficePreviewWidth(container) {
  const rectWidth = Math.floor(container.getBoundingClientRect().width || 0);
  return Math.max(900, rectWidth || 1200);
}

function getOnlyOfficePreviewHeight(container) {
  if (document.fullscreenElement === container) {
    return Math.max(720, window.innerHeight || 900);
  }
  const viewportHeight = window.innerHeight || 980;
  return Math.max(760, viewportHeight - 330);
}

function requestOnlyOfficeResize() {
  [100, 400, 1200, 2500].forEach((delay) => {
    setTimeout(() => {
      window.dispatchEvent(new Event("resize"));
      state.onlyOfficeEditor?.resizeEditor?.();
    }, delay);
  });
}

function renderOnlyOfficeFallback(message) {
  const container = $("#onlyoffice-preview");
  if (!container) return;
  const downloadUrl = state.wordSummary?.download_url || (state.selectedProjectId ? `/api/v1/projects/${state.selectedProjectId}/word/download?version=latest` : "");
  container.innerHTML = `
    <div class="onlyoffice-fallback">
      <h3>在线预览暂不可用</h3>
      <p>${escapeHtml(message || "OnlyOffice 尚未接入。")}</p>
      <p class="toc-help-text">目录页码可在 WPS 或 OnlyOffice 打开后更新：点击目录区域，选择“更新目录/更新页码”，再保存文档。</p>
      ${downloadUrl ? `<a class="button-link" href="${escapeHtml(downloadUrl)}" download>下载最新版 Word</a>` : ""}
    </div>
  `;
}

function renderWordExportFallback(message) {
  const statusText = $("#word-status-text");
  if (statusText) statusText.textContent = message;
  renderOnlyOfficeFallback(message);
}

function renderArtifacts(artifacts) {
  resetArtifactPreviews(artifacts);
  renderGenerationResultActions(artifacts);
  const preview = $("#chapter-preview");
  const draft = artifacts.llm_draft_markdown || artifacts.draft_markdown;
  if (preview && draft) {
    loadArtifactPreview(artifacts.llm_draft_markdown ? "llm_draft_markdown" : "draft_markdown", "#chapter-preview", { compact: true });
  }
}

function resetArtifactPreviews(artifacts) {
  const parsePreview = $("#parse-artifact-preview");
  const outlinePreview = $("#outline-artifact-preview");
  const chapterPreview = $("#chapter-preview");
  if (parsePreview) {
    parsePreview.innerHTML = "";
  }
  if (outlinePreview && !artifacts.outline_report) {
    outlinePreview.innerHTML = '<div class="mini-empty">目录生成后，这里展示目录报告。</div>';
  }
  if (chapterPreview && !artifacts.draft_markdown && !artifacts.llm_draft_markdown) {
    chapterPreview.innerHTML = `
      <h3>土建施工方案与技术措施</h3>
      <p>本章将结合招标文件技术要求、投标知识库和项目基础信息生成正文、表格、图片引用及人工复核提示。</p>
      <p>生成结果会优先保证评分点覆盖完整，再进行图文并茂和 Word 格式优化。</p>
    `;
  }
}

function renderArtifactList(selector, entries, artifacts, previewSelector) {
  const container = $(selector);
  if (!container) return;
  const rows = entries
    .map(([key, label]) => {
      const item = artifacts[key];
      if (!item) return "";
      const downloadUrl = item.download_url || (state.selectedProjectId ? artifactDownloadUrl(state.selectedProjectId, key) : "");
      return `
        <div class="artifact-item">
          <strong>${escapeHtml(label)}</strong>
          <small>${escapeHtml(item.file_name || "")} · ${fmtNumber(item.size)} bytes</small>
          <code>${escapeHtml(item.storage_uri || "")}</code>
          <div class="artifact-actions">
            ${item.previewable ? `<button type="button" class="secondary small-button" data-preview-artifact="${escapeHtml(key)}" data-preview-target="${escapeHtml(previewSelector || "")}">查看</button>` : ""}
            ${downloadUrl ? `<a class="download-link" href="${escapeHtml(downloadUrl)}" download>下载</a>` : ""}
          </div>
        </div>
      `;
    })
    .join("");
  container.innerHTML = rows || '<div class="mini-empty">当前步骤暂无产物。</div>';
  container.querySelectorAll("[data-preview-artifact]").forEach((button) => {
    button.addEventListener("click", () => loadArtifactPreview(button.dataset.previewArtifact, button.dataset.previewTarget));
  });
}

function renderArtifactDownloadButtons(artifacts) {
  $$("[data-artifact-download]").forEach((link) => {
    const key = link.dataset.artifactDownload;
    const item = artifacts[key];
    if (item?.download_url) {
      link.href = item.download_url;
      link.setAttribute("download", item.file_name || "");
      link.classList.remove("disabled");
      link.setAttribute("aria-disabled", "false");
    } else {
      link.removeAttribute("href");
      link.removeAttribute("download");
      link.classList.add("disabled");
      link.setAttribute("aria-disabled", "true");
    }
  });
}

async function loadArtifactPreview(key, targetSelector, options = {}) {
  if (!state.selectedProjectId || !key || !targetSelector) return;
  const target = $(targetSelector);
  if (!target) return;
  const cacheKey = `${state.selectedProjectId}:${key}`;
  if (!options.silent) {
    target.innerHTML = '<div class="mini-empty">正在读取产物内容...</div>';
  }
  try {
    const artifact = state.artifactPreviewCache[cacheKey] || await api(artifactUrl(state.selectedProjectId, key));
    state.artifactPreviewCache[cacheKey] = artifact;
    target.innerHTML = renderArtifactPreview(artifact, options);
  } catch (error) {
    if (!options.silent) {
      target.innerHTML = `<div class="mini-empty">产物预览失败：${escapeHtml(error.message)}</div>`;
    }
  }
}

function renderArtifactPreview(artifact, options = {}) {
  const title = options.compact ? "" : `<div class="artifact-preview-head">
    <div>
      <strong>${escapeHtml(artifact.label || artifact.file_name || "产物预览")}</strong>
      <small>${escapeHtml(artifact.file_name || "")} · ${fmtNumber(artifact.size)} bytes</small>
    </div>
    ${artifact.download_url ? `<a class="download-link" href="${escapeHtml(artifact.download_url)}" download>下载</a>` : ""}
  </div>`;
  if (artifact.render_type === "markdown") {
    return `${title}<div class="markdown-preview">${simpleMarkdownToHtml(artifact.text || "")}</div>`;
  }
  if (artifact.render_type === "json") {
    return `${title}<pre class="json-preview">${escapeHtml(artifact.text || "")}</pre>`;
  }
  return `${title}<pre class="json-preview">${escapeHtml(artifact.text || "")}</pre>`;
}

function renderReviewItems(items) {
  const list = $("#review-items-list");
  if (!list) return;
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<div class="mini-empty">生成 Word 初稿后，这里展示自动复核项。</div>';
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = `review-item ${item.severity || "medium"}`;
    row.innerHTML = `
      <span>${escapeHtml(item.severity || "medium")}</span>
      <strong>${escapeHtml(item.title)}</strong>
    `;
    list.appendChild(row);
  }
}

function renderLatestJobs(jobs) {
  const panel = $("#job-monitor");
  const list = $("#latest-jobs");
  if (!panel || !list) return;
  maybeReportCompletedAiJob(jobs);
  renderGenerationCurrentJob(jobs);
  updateGenerationActionState();
  if (!jobs.length) {
    panel.classList.add("hidden");
    list.innerHTML = "";
    return;
  }
  panel.classList.remove("hidden");
  const activeCount = jobs.filter(isActiveJob).length;
  $("#job-monitor-status").textContent = activeCount ? `${activeCount} 个任务执行中` : "最近任务";
  const primary = jobs.find(isActiveJob) || jobs[0];
  const history = jobs.slice(1, 6);
    list.innerHTML = `
      ${renderCompactJob(primary, true)}
      ${
        history.length
        ? `<details class="job-history"><summary>查看历史任务 ${history.length} 条</summary>${history.map((job) => renderCompactJob(job, false)).join("")}</details>`
        : ""
      }
    `;
    list.querySelectorAll("[data-cancel-job]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        cancelJob(button.dataset.cancelJob);
      });
    });
    list.querySelectorAll("[data-retry-outline]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        createJob("outline_generation", button);
      });
    });
  }

  function renderCompactJob(job, primary) {
  const percent = Math.max(0, Math.min(100, Number(job.progress_percent ?? (job.status === "succeeded" ? 100 : 0))));
  const message = job.error_message || job.message || "";
  const showCountProgress = job.job_type === "chapter_llm_generation";
  const progressLabel = showCountProgress ? "小节包 " : "";
  const retrying = Number(job.metadata?.retrying || 0);
  const canRetryOutline = job.job_type === "outline_generation" && String(job.status || "") === "failed";
  return `
    <article class="job-item compact ${primary ? "primary" : ""} ${escapeHtml(job.status || "pending")}">
      <div class="job-head">
        <div>
          <strong>${escapeHtml(job.job_label || job.job_type || "任务")}</strong>
          <small>${escapeHtml(message)}</small>
        </div>
        <span>${escapeHtml(job.status_label || fmtJobStatus(job.status))}</span>
      </div>
      ${isActiveJob(job) ? `<div class="progress-bar"><i style="width: ${percent}%"></i></div>` : ""}
      <div class="job-meta">
        <span>${percent.toFixed(percent % 1 ? 1 : 0)}%</span>
        ${showCountProgress && Number.isFinite(Number(job.progress_completed)) && Number.isFinite(Number(job.progress_total)) ? `<span>${escapeHtml(progressLabel)}${escapeHtml(job.progress_completed)} / ${escapeHtml(job.progress_total)}</span>` : ""}
        ${retrying ? `<span>自动重试中 ${escapeHtml(retrying)}</span>` : ""}
        ${job?.metadata?.max_workers ? `<span>并发 ${escapeHtml(job.metadata.max_workers)}</span>` : ""}
        <span>${escapeHtml(fmtTime(job.updated_at))}</span>
      </div>
      ${
        isActiveJob(job) || canRetryOutline
          ? `<div class="job-actions">
              ${isActiveJob(job) ? `<button type="button" class="secondary small-button" data-cancel-job="${escapeHtml(job.job_id || "")}">停止</button>` : ""}
              ${canRetryOutline ? `<button type="button" class="secondary small-button" data-retry-outline>重新生成目录</button>` : ""}
            </div>`
          : ""
      }
    </article>
    `;
  }

function maybeReportCompletedAiJob(jobs = []) {
  for (const job of jobs || []) {
    const jobId = String(job.job_id || "");
    if (!jobId) continue;
    const previous = state.knownJobStatuses.get(jobId);
    const current = String(job.status || "");
    state.knownJobStatuses.set(jobId, current);
    if (state.assistantReportedJobs.has(jobId)) continue;
    if (!shouldAssistantReportJob(job)) continue;
    const completedAfterRunning = previous && previous !== current && ["pending", "running"].includes(previous);
    const justCreatedSync = !previous && ["succeeded", "failed"].includes(current);
    if (!completedAfterRunning && !justCreatedSync) continue;
    state.assistantReportedJobs.add(jobId);
    showAssistantJobReport(job);
    break;
  }
}

function shouldAssistantReportJob(job) {
  const type = String(job?.job_type || "");
  const status = String(job?.status || "");
  if (!["succeeded", "failed"].includes(status)) return false;
  return ["chapter_llm_generation", "chapter_aggregate_refresh"].includes(type);
}

function showAssistantJobReport(job) {
  const answer = $("#assistant-chat-answer");
  if (!answer) return;
  toggleAssistantDock(true);
  refreshAiContext({ silent: true });
  answer.innerHTML = renderAssistantJobReport(job);
}

function renderAssistantJobReport(job) {
  const meta = job.metadata || {};
  const usage = meta.llm_usage_summary || {};
  const duration = fmtDurationSeconds(meta.duration_seconds || jobDurationSeconds(job));
  const stageLines = assistantJobTimingLines(job, duration);
  const completed = Number(job.progress_completed ?? meta.completed_count ?? 0);
  const total = Number(job.progress_total ?? meta.generation_unit_count ?? 0);
  const failed = Number(job.progress_failed ?? meta.failed_count ?? 0);
  const skipped = Number(meta.skipped_count || 0);
  const tokenInfo = estimateJobTokenUsage(job);
  const title = job.status === "succeeded" ? "本次生成已经完成" : "本次生成有小节需要处理";
  const nextTip = failed > 0
    ? "建议先点击“重试失败”，处理失败小节后再刷新 Word 初稿。"
    : "建议进入 Word 初稿页，检查目录、表格、图片和评分点覆盖情况。";
  const lines = [
    ...stageLines,
    total ? `处理 ${completed}/${total} 个小节包` : `${completed} 个小节包已处理`,
    failed ? `失败 ${failed} 个` : "失败 0 个",
    skipped ? `跳过 ${skipped} 个` : "",
    usage.call_count ? `模型调用 ${fmtNumber(usage.call_count)} 次` : "",
    usage.failed_count ? `模型失败 ${fmtNumber(usage.failed_count)} 次` : "",
    tokenInfo.label,
  ].filter(Boolean);
  return `
    <strong>${escapeHtml(title)}</strong>
    <small>${escapeHtml(lines.join(" · "))}</small>
    <p>${escapeHtml(nextTip)}</p>
  `;
}

function assistantJobTimingLines(job, duration) {
  const type = String(job?.job_type || "");
  const meta = job?.metadata || {};
  if (type === "chapter_aggregate_refresh") {
    const refresh = meta.refresh_timing || {};
    const refreshDuration = fmtDurationSeconds(refresh.duration_seconds) || duration;
    return [refreshDuration ? `Word 整理 ${refreshDuration}（不调用大模型）` : "Word 整理耗时暂未记录"];
  }
  if (type === "chapter_llm_generation") {
    const lines = [];
    if (duration) lines.push(`任务总耗时 ${duration}`);
    const wordTiming = meta.workflow_refresh_timing || meta.word_refresh_timing || {};
    const wordDuration = fmtDurationSeconds(wordTiming.duration_seconds);
    if (wordDuration) lines.push(`Word 整理 ${wordDuration}`);
    return lines.length ? lines : ["耗时暂未记录"];
  }
  return [duration ? `耗时 ${duration}` : "耗时暂未记录"];
}

function estimateJobTokenUsage(job) {
  const meta = job.metadata || {};
  const usage = meta.llm_usage_summary || {};
  const audited = Number(usage.estimated_total_tokens || 0);
  if (audited > 0) return { label: `估算 token 约 ${fmtNumber(audited)}` };
  const exact = Number(meta.total_tokens || meta.token_count || 0);
  if (exact > 0) return { label: `共消耗 token ${fmtNumber(exact)}` };
  const totalUnits = Number(job.progress_total || meta.generation_unit_count || meta.completed_count || 0);
  const maxTokens = Number(state.modelConfig?.tasks?.technical_bid_chapter_generation?.max_tokens || 0);
  if (maxTokens > 0 && totalUnits > 0) {
    const estimated = Math.round(totalUnits * maxTokens * 0.75);
    return { label: `估算 token 约 ${fmtNumber(estimated)}` };
  }
  return { label: "token 暂无精确统计" };
}

function jobDurationSeconds(job) {
  const start = job?.started_at ? new Date(job.started_at).getTime() : 0;
  const end = job?.ended_at ? new Date(job.ended_at).getTime() : 0;
  if (!start || !end || end < start) return null;
  return (end - start) / 1000;
}

function updateWorkflowPolling(jobs) {
  const shouldPoll = jobs.some(isActiveJob);
  const fastPoll = (jobs || []).some((job) => job.job_type === "chapter_llm_generation" && isActiveJob(job));
  const intervalMs = fastPoll ? 1000 : 1500;
  if (shouldPoll && state.pollingTimer && state.currentWorkflowPollMs !== intervalMs) {
    window.clearInterval(state.pollingTimer);
    state.pollingTimer = null;
  }
  if (shouldPoll && !state.pollingTimer) {
    state.currentWorkflowPollMs = intervalMs;
    state.pollingTimer = window.setInterval(async () => {
      if (!state.selectedProjectId || state.pollingInFlight) return;
      state.pollingInFlight = true;
      try {
        await refreshWorkflowSummary(state.selectedProjectId);
        await refreshAiContext({ silent: true });
      } finally {
        state.pollingInFlight = false;
      }
    }, intervalMs);
  }
  if (!shouldPoll && state.pollingTimer) {
    window.clearInterval(state.pollingTimer);
    state.pollingTimer = null;
    state.currentWorkflowPollMs = 1500;
  }
}

async function createProject(event) {
  event.preventDefault();
  const formElement = event.currentTarget || event.target || $("#project-form");
  const form = new FormData(formElement);
  const payload = {
    name: form.get("name"),
    project_type: form.get("project_type") || null,
    description: form.get("description") || null,
  };
  try {
    const project = await api("/api/v1/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.selectedProjectId = project.project_id;
    state.activeStep = "upload";
    state.activeStepPinned = false;
    formElement?.reset?.();
    toast("项目已创建");
    await refreshProjects();
  } catch (error) {
    toast(`创建失败：${error.message}`);
  }
}

async function deleteCurrentProject() {
  const project = currentProject();
  if (!project) {
    toast("请先选择项目");
    return;
  }
  const typed = window.prompt(`删除项目会同时删除该项目的上传文件、任务记录和生成产物。\n请输入项目名称确认删除：${project.name}`);
  if (typed === null) return;
  if (typed.trim() !== project.name) {
    toast("项目名称不一致，已取消删除");
    return;
  }
  try {
    await api(`/api/v1/projects/${encodeURIComponent(project.project_id)}`, { method: "DELETE" });
    state.selectedProjectId = null;
    state.workflowSummary = null;
    state.files = [];
    state.wordSummary = null;
    state.wordProfile = null;
    state.onlyOfficeConfig = null;
    state.aiSummary = null;
    state.bidTemplateRecommendation = null;
    state.ragMaterials = null;
    state.selectedGenerationUnits = new Set();
    state.artifactPreviewCache = {};
    updateWorkflowPolling([]);
    toast("项目已删除");
    await refreshProjects({ autoSelect: false, refreshWorkspace: true });
  } catch (error) {
    toast(`删除项目失败：${error.message}`);
  }
}

async function deleteUploadedFile(fileId) {
  if (!state.selectedProjectId || !fileId) return;
  const file = state.files.find((item) => item.file_id === fileId);
  if (!file) return;
  const confirmed = window.confirm(`确认删除已上传文件？\n${file.file_name}`);
  if (!confirmed) return;
  try {
    await api(`/api/v1/projects/${encodeURIComponent(state.selectedProjectId)}/files/${encodeURIComponent(fileId)}`, {
      method: "DELETE",
    });
    state.artifactPreviewCache = {};
    state.activeStepPinned = false;
    toast("文件已删除");
    await refreshProjectSnapshot({ silent: true });
  } catch (error) {
    toast(`删除文件失败：${error.message}`);
  }
}

async function uploadFile(event) {
  event.preventDefault();
  if (!state.selectedProjectId) {
    toast("请先创建或选择项目");
    return;
  }
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  try {
    const uploaded = await api(`/api/v1/projects/${state.selectedProjectId}/files`, {
      method: "POST",
      body: form,
    });
    state.artifactPreviewCache = {};
    state.activeStepPinned = false;
    formElement.reset();
    toast("资料已上传");
    await refreshProjectSnapshot({ silent: true });
    return uploaded;
  } catch (error) {
    toast(`上传失败：${error.message}`);
    try {
      await refreshFiles(state.selectedProjectId);
    } catch (_ignored) {
      // 上传失败后尝试刷新列表，仅用于纠正“实际已保存但响应异常”的边界情况。
    }
    return null;
  }
}

async function refreshWorkspaceAfterMutation(successMessage, failurePrefix) {
  try {
    await refreshProjectSnapshot({ silent: true });
    if (successMessage) toast(successMessage);
  } catch (error) {
    toast(`${failurePrefix}：${error.message}`);
  }
}

async function createJob(jobType, button) {
  if (!state.selectedProjectId) {
    toast("请先选择项目");
    return;
  }
  const originalText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "处理中";
  }
  try {
    const job = await api(`/api/v1/projects/${state.selectedProjectId}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_type: jobType }),
    });
    const output = $("#parse-job-output");
    if (output && jobType === "tender_parse") {
      output.textContent = JSON.stringify(job, null, 2);
    }
    state.artifactPreviewCache = {};
    state.activeStepPinned = false;
    toast(job.status === "succeeded" ? "任务已完成" : "任务已创建，正在后台执行");
    await refreshProjectSnapshot({ silent: true });
  } catch (error) {
    toast(`任务创建失败：${error.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      updateWorkflowActionButtons(state.workflowSummary);
      updateGenerationActionState();
      if (!state.workflowSummary) button.textContent = originalText;
    }
  }
}

async function cancelJob(jobId) {
  if (!jobId) return;
  if (!window.confirm("确认停止当前任务？已完成的章节会保留，后续可以断点续跑。")) {
    return;
  }
  try {
    await api(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
    toast("任务已标记为停止");
    await refreshProjectSnapshot({ silent: true });
  } catch (error) {
    toast(`停止任务失败：${error.message}`);
  }
}

async function createChapterLlmJob({ runAll = false, targetIds = null, label = "选中小节包", button = null, retryFailedOnly = false } = {}) {
  if (!state.selectedProjectId) {
    toast("请先选择项目");
    return;
  }
  const selectedIds = targetIds ? [...targetIds] : [...state.selectedGenerationUnits];
  if (!runAll && !selectedIds.length) {
    toast("请先选择要生成的小节包");
    return;
  }
  const units = state.workflowSummary?.generation_units || [];
  const idSet = new Set(selectedIds.map(String));
  const generatedSelected = units.filter((item) => idSet.has(String(item.unit_id || "")) && isGeneratedGenerationUnit(item));
  if (!runAll && generatedSelected.length) {
    toast(`已生成小节包默认不重跑，已拦截 ${generatedSelected.length} 个。`);
    return null;
  }
  if (runAll && units.length > 20 && !window.confirm(`将生成全部 ${units.length} 个小节包，可能耗时较长。确认继续？`)) {
    return;
  }
  const originalText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "生成中";
  }
  try {
    const job = await api(`/api/v1/projects/${state.selectedProjectId}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_type: "chapter_llm_generation",
        run_all: runAll,
        target_unit_ids: runAll ? [] : selectedIds,
        retry_failed_only: retryFailedOnly,
      }),
    });
    state.artifactPreviewCache = {};
    state.activeStepPinned = false;
    toast(runAll ? "已开始生成全部小节包" : `已开始生成 ${selectedIds.length} 个${label}`);
    await refreshProjectSnapshot({ silent: true });
    return job;
  } catch (error) {
    toast(`正文生成任务创建失败：${error.message}`);
    return null;
  } finally {
    if (button) {
      button.disabled = false;
      updateGenerationActionState();
      if (!state.workflowSummary) button.textContent = originalText;
    }
  }
}

async function generateUnfinishedChapters(button = null) {
  const units = state.workflowSummary?.generation_units || [];
  const ids = units.filter(isUnfinishedGenerationUnit).map((item) => String(item.unit_id || "")).filter(Boolean);
  if (!ids.length) {
    toast("没有未完成小节包需要继续生成");
    return null;
  }
  state.selectedGenerationUnits = new Set(ids);
  renderGenerationUnits(units);
  return createChapterLlmJob({ targetIds: ids, label: "未完成小节包", button });
}

async function retryFailedChapters(button = null) {
  const units = state.workflowSummary?.generation_units || [];
  const ids = units.filter(isFailedGenerationUnit).map((item) => String(item.unit_id || "")).filter(Boolean);
  if (!ids.length) {
    toast("没有失败小节包需要重试");
    return null;
  }
  state.selectedGenerationUnits = new Set(ids);
  renderGenerationUnits(units);
  return createChapterLlmJob({ targetIds: ids, label: "失败小节包", button, retryFailedOnly: true });
}

function setActiveStep(step, options = {}) {
  state.activeStep = step;
  state.activeStepPinned = options.userInitiated !== false;
  $$(".tab").forEach((button) => button.classList.toggle("active", button.dataset.step === step));
  $$(".step-pane").forEach((pane) => pane.classList.toggle("active", pane.id === `step-${step}`));
  $("#project-workspace")?.classList.toggle("review-mode", step === "review");
  renderWorkflowSteps(state.workflowSummary?.steps || []);
  renderStageBanner(state.workflowSummary?.latest_jobs || []);
  if (step === "review" && options.refreshStepData !== false) {
    refreshWordExportPage();
  }
  renderTopProjectContext();
  renderAssistantPageAdvice();
  renderAiAssistantPanel();
  renderAiInsightPanel();
  if (state.selectedProjectId && options.userInitiated !== false) {
    refreshAiContext({ silent: true });
  }
}

async function refreshModelConfig() {
  try {
    const config = await api("/api/v1/model-config");
    state.modelConfig = config;
    const form = $("#model-form");
    form.provider.value = config.provider || "";
    form.api_type.value = config.api_type || "responses";
    form.base_url.value = config.base_url || "";
    form.model.value = config.model || "";
    form.api_key.placeholder = config.api_key_masked || "留空则不修改";
    form.temperature.value = config.effective_default?.temperature ?? "";
    form.top_p.value = config.effective_default?.top_p ?? "";
    form.timeout_seconds.value = config.effective_default?.timeout_seconds ?? "";
    form.max_retries.value = config.effective_default?.max_retries ?? "";
    form.max_workers.value = config.effective_default?.max_workers ?? "";
    form.structured_output_type.value = config.effective_default?.structured_output_type || "";
    form.enable_thinking.checked = Boolean(config.effective_default?.enable_thinking);
    $("#profile-path").textContent = config.task_profiles_path || "";
    renderProfiles(config.tasks || {});
  } catch (error) {
    toast(`AI 服务设置加载失败：${userFacingErrorMessage(error)}`);
  }
}

async function refreshExcellentBidLibrary() {
  try {
    const manifest = await api("/api/v1/rag/sources");
    state.excellentBidLibrary = manifest;
    renderExcellentBidLibrary(manifest);
  } catch (error) {
    toast(`投标知识库加载失败：${error.message}`);
  }
}

async function refreshBidTemplates() {
  try {
    const payload = await api("/api/v1/bid-templates");
    state.bidTemplates = payload.templates || [];
    if (!state.selectedBidTemplateId && state.bidTemplates.length) {
      state.selectedBidTemplateId = state.bidTemplates[0].template_id;
    }
    renderBidTemplates();
    renderBidTemplateDetail();
    await refreshBidTemplateRecommendation();
  } catch (error) {
    toast(`投标模板加载失败：${userFacingErrorMessage(error)}`);
  }
}

async function refreshBidTemplateRecommendation() {
  const status = $("#bid-template-project-status");
  if (!state.selectedProjectId) {
    if (status) status.textContent = "选择项目后展示推荐。";
    const container = $("#bid-template-project-recommendations");
    if (container) container.innerHTML = '<div class="mini-empty">暂无项目上下文。</div>';
    return;
  }
  try {
    const recommendation = await api(`/api/v1/projects/${state.selectedProjectId}/bid-template/recommendation`);
    state.bidTemplateRecommendation = recommendation;
    renderBidTemplateRecommendation();
  } catch (error) {
    if (status) status.textContent = `推荐失败：${userFacingErrorMessage(error)}`;
  }
}

async function refreshAccounts() {
  try {
    const payload = await api("/api/v1/accounts");
    state.accounts = payload.accounts || [];
    renderAccounts(payload);
  } catch (error) {
    toast(`账户管理加载失败：${userFacingErrorMessage(error)}`);
  }
}

function renderAccounts(payload = {}) {
  const accounts = payload.accounts || state.accounts || [];
  const active = accounts.filter((item) => item.status === "active").length;
  const disabled = accounts.filter((item) => item.status === "disabled").length;
  $("#account-total").textContent = fmtNumber(accounts.length);
  $("#account-active").textContent = fmtNumber(active);
  $("#account-disabled").textContent = fmtNumber(disabled);
  $("#account-status").textContent = payload.auth_enforced ? "已启用登录鉴权" : "管理台账模式";
  const container = $("#account-list");
  if (!container) return;
  container.innerHTML = accounts.length
    ? accounts.map((item) => `
      <article class="account-row ${escapeHtml(item.status || "")}">
        <div class="account-person">
          <span class="account-avatar">${escapeHtml(accountInitial(item.display_name || item.username))}</span>
          <div>
            <strong>${escapeHtml(item.display_name || item.username)}</strong>
            <small>${escapeHtml(item.username || "")}</small>
          </div>
        </div>
        <span>${escapeHtml(item.role_label || item.role || "-")}</span>
        <span>${escapeHtml(item.department || "未填写部门")}</span>
        <span class="status-pill ${item.status === "active" ? "ok" : "pending"}">${escapeHtml(item.status_label || item.status || "-")}</span>
        <div class="button-row tight">
          <button class="secondary small-button" type="button" data-account-toggle="${escapeHtml(item.account_id)}" data-next-status="${item.status === "active" ? "disabled" : "active"}">
            ${item.status === "active" ? "停用" : "启用"}
          </button>
        </div>
      </article>
    `).join("")
    : '<div class="mini-empty">暂无账户。</div>';
  container.querySelectorAll("[data-account-toggle]").forEach((button) => {
    button.addEventListener("click", () => updateAccountStatus(button.dataset.accountToggle, button.dataset.nextStatus));
  });
}

function accountInitial(value) {
  const text = String(value || "账").trim();
  return text.slice(0, 1).toUpperCase();
}

async function createAccount(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button[type="submit"]');
  const payload = Object.fromEntries(new FormData(form).entries());
  for (const key of ["department", "phone", "email"]) {
    if (!payload[key]) payload[key] = null;
  }
  submit.disabled = true;
  try {
    await api("/api/v1/accounts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    form.reset();
    toast("账户已创建");
    await refreshAccounts();
  } catch (error) {
    toast(`创建账户失败：${error.message}`);
  } finally {
    submit.disabled = false;
  }
}

async function updateAccountStatus(accountId, status) {
  try {
    await api(`/api/v1/accounts/${encodeURIComponent(accountId)}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    toast(status === "active" ? "账户已启用" : "账户已停用");
    await refreshAccounts();
  } catch (error) {
    toast(`更新账户失败：${error.message}`);
  }
}

function setAuthMode(mode) {
  const isRegister = mode === "register";
  $("#auth-login-tab")?.classList.toggle("active", !isRegister);
  $("#auth-register-tab")?.classList.toggle("active", isRegister);
  $("#login-form")?.classList.toggle("hidden", isRegister);
  $("#register-form")?.classList.toggle("hidden", !isRegister);
  const message = $("#auth-message");
  if (message) {
    message.textContent = isRegister ? "注册后默认作为编标人员使用，角色可由管理员调整。" : "请输入企业内部分配的账号和密码。";
  }
}

async function login(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const payload = await api("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: form.username.value,
        password: form.password.value,
      }),
      skipAuthRedirect: true,
    });
    state.currentAccount = payload.account;
    hideAuthScreen();
    applyAuthUi();
    toast("已登录");
    await loadWorkbenchData();
  } catch (error) {
    showAuthScreen(error.message);
  }
}

async function register(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const payload = await api("/api/v1/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: form.username.value,
        password: form.password.value,
        display_name: form.display_name.value,
        department: form.department.value || null,
      }),
      skipAuthRedirect: true,
    });
    state.currentAccount = payload.account;
    hideAuthScreen();
    applyAuthUi();
    toast("已注册并登录");
    await loadWorkbenchData();
  } catch (error) {
    showAuthScreen(error.message);
  }
}

async function logout() {
  try {
    await api("/api/v1/auth/logout", { method: "POST", skipAuthRedirect: true });
  } catch (error) {
    console.warn(error);
  }
  state.currentAccount = null;
  state.projects = [];
  state.selectedProjectId = null;
  showAuthScreen("已退出，请重新登录。");
  applyAuthUi();
}

function renderBidTemplates() {
  const container = $("#bid-template-list");
  const status = $("#bid-template-status");
  if (!container) return;
  const templates = filteredBidTemplates();
  const total = (state.bidTemplates || []).length;
  if (status) status.textContent = `${templates.length} / ${total} 个模板`;
  container.innerHTML = templates.length
    ? templates
        .map(
          (item) => `
            <article class="template-card${item.template_id === state.selectedBidTemplateId ? " selected" : ""}">
              <div class="template-card-head">
                <strong>${escapeHtml(item.name || "未命名模板")}</strong>
                <span>${escapeHtml(item.version || "-")}</span>
              </div>
              <p>${escapeHtml(item.description || "暂无说明。")}</p>
              <div class="slice-meta">
                <span>${escapeHtml(fmtProjectType(item.project_type))}</span>
                <span>${escapeHtml(item.chapter_count || 0)} 个章节</span>
                <span>${escapeHtml(item.table_count || (item.tables || []).length || 0)} 个表格</span>
                ${(item.tags || []).slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
              </div>
              <div class="template-card-actions">
                <button class="secondary small-button" type="button" data-template-select="${escapeHtml(item.template_id || "")}">查看详情</button>
                <button class="ghost-button small-button" type="button" data-template-ask="${escapeHtml(item.template_id || "")}">问小智怎么用</button>
              </div>
            </article>
          `,
        )
        .join("")
    : '<div class="empty-state">暂无投标模板。</div>';
  container.querySelectorAll("[data-template-select]").forEach((button) => {
    button.addEventListener("click", () => selectBidTemplate(button.dataset.templateSelect));
  });
  container.querySelectorAll("[data-template-ask]").forEach((button) => {
    button.addEventListener("click", () => askAssistantAboutTemplate(button.dataset.templateAsk));
  });
}

function renderBidTemplateRecommendation() {
  const container = $("#bid-template-project-recommendations");
  const status = $("#bid-template-project-status");
  if (!container) return;
  const items = state.bidTemplateRecommendation?.recommendations || [];
  if (status) status.textContent = currentProject() ? `当前项目：${currentProject().name}` : "选择项目后展示推荐。";
  container.innerHTML = items.length
    ? items
        .slice(0, 5)
        .map(
          (item) => `
            <div class="template-recommendation-item ${escapeHtml(item.fit_level || "reference")}">
              <div>
                <strong>${escapeHtml(item.name || "未命名模板")}</strong>
                <small>${escapeHtml(item.reason || "可作为结构参考")}</small>
              </div>
              <span>${escapeHtml(item.fit_level_label || "仅作参考")}</span>
              <p>${(item.coverage_tips || []).slice(0, 3).map((tip) => `<em>${escapeHtml(tip)}</em>`).join("")}</p>
              <div class="template-card-actions">
                <button class="secondary small-button" type="button" data-template-select="${escapeHtml(item.template_id || "")}">查看模板</button>
                <button class="ghost-button small-button" type="button" data-template-ask="${escapeHtml(item.template_id || "")}">问小智</button>
              </div>
            </div>
          `,
        )
        .join("")
    : '<div class="mini-empty">暂无可推荐模板。</div>';
  container.querySelectorAll("[data-template-select]").forEach((button) => {
    button.addEventListener("click", () => selectBidTemplate(button.dataset.templateSelect));
  });
  container.querySelectorAll("[data-template-ask]").forEach((button) => {
    button.addEventListener("click", () => askAssistantAboutTemplate(button.dataset.templateAsk));
  });
}

function filteredBidTemplates() {
  const filters = state.bidTemplateFilters || {};
  const query = String(filters.query || "").trim().toLowerCase();
  const projectType = String(filters.project_type || "");
  const tag = String(filters.tag || "").trim().toLowerCase();
  return (state.bidTemplates || []).filter((item) => {
    const text = [
      item.name,
      item.description,
      item.project_type,
      ...(item.tags || []),
      ...(item.tables || []),
      ...(item.applicable_scenarios || []),
      ...(item.chapters || []).map((chapter) => `${chapter.title || ""} ${(chapter.writing_focus || []).join(" ")}`),
    ].join(" ").toLowerCase();
    if (query && !text.includes(query)) return false;
    if (projectType && item.project_type !== projectType) return false;
    if (tag && !(item.tags || []).some((value) => String(value).toLowerCase().includes(tag))) return false;
    return true;
  });
}

function selectedBidTemplate() {
  const templates = state.bidTemplates || [];
  return templates.find((item) => item.template_id === state.selectedBidTemplateId) || templates[0] || null;
}

function selectBidTemplate(templateId) {
  if (templateId) state.selectedBidTemplateId = templateId;
  renderBidTemplates();
  renderBidTemplateDetail();
}

function renderBidTemplateDetail() {
  const container = $("#bid-template-detail");
  const status = $("#bid-template-detail-status");
  if (!container) return;
  const template = selectedBidTemplate();
  if (!template) {
    if (status) status.textContent = "暂无模板。";
    container.innerHTML = '<div class="mini-empty">上传或选择模板后，这里展示章节结构、写作重点和表格清单。</div>';
    return;
  }
  if (status) status.textContent = `${fmtProjectType(template.project_type)} · ${template.version || "v1"}`;
  const chapters = template.chapters || [];
  const tables = template.tables || template.table_templates || [];
  const scenarios = template.applicable_scenarios || [];
  container.innerHTML = `
    <div class="template-detail-summary">
      <span><strong>${escapeHtml(template.chapter_count || chapters.length || 0)}</strong>章节</span>
      <span><strong>${escapeHtml(template.table_count || tables.length || 0)}</strong>表格</span>
      <span><strong>${escapeHtml((template.tags || []).length)}</strong>标签</span>
    </div>
    <div class="template-boundary-note">${escapeHtml(template.usage_boundary || "模板只做推荐和预览，不自动覆盖已确认目录或正文。")}</div>
    ${scenarios.length ? `<div class="template-section-block"><strong>适用场景</strong>${scenarios.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}
    <div class="template-section-block">
      <strong>章节结构与写作重点</strong>
      ${
        chapters.length
          ? chapters.slice(0, 12).map((chapter, index) => `
              <article>
                <i>${index + 1}</i>
                <div>
                  <b>${escapeHtml(chapter.title || "未命名章节")}</b>
                  <small>${escapeHtml((chapter.writing_focus || []).join("；") || "结合当前项目评分点补充。")}</small>
                </div>
              </article>
            `).join("")
          : '<div class="mini-empty">暂无章节结构。</div>'
      }
    </div>
    <div class="template-section-block">
      <strong>表格清单</strong>
      <div class="template-table-tags">${tables.length ? tables.map((item) => `<span>${escapeHtml(item)}</span>`).join("") : '<span>暂无表格清单</span>'}</div>
    </div>
  `;
}

function handleBidTemplateFilters(event) {
  const form = event.currentTarget;
  const data = new FormData(form);
  state.bidTemplateFilters = {
    query: String(data.get("query") || ""),
    project_type: String(data.get("project_type") || ""),
    tag: String(data.get("tag") || ""),
  };
  renderBidTemplates();
}

function askAssistantAboutTemplate(templateId = "") {
  if (templateId) {
    state.selectedBidTemplateId = templateId;
    renderBidTemplates();
    renderBidTemplateDetail();
  }
  const template = selectedBidTemplate();
  const prompt = template
    ? `这个投标模板“${template.name}”应该怎么用于当前项目？能不能直接覆盖目录？`
    : "当前项目推荐使用哪个投标模板？";
  const input = assistantChatInput();
  if (input) input.value = prompt;
  submitAssistantQuestion(prompt, input);
}

function openBidTemplateUploadDialog() {
  $("#bid-template-upload-dialog")?.showModal();
}

function closeBidTemplateUploadDialog() {
  $("#bid-template-upload-dialog")?.close();
}

async function uploadBidTemplate(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button[type="submit"]');
  const status = $("#bid-template-upload-status");
  const payload = new FormData(form);
  submit.disabled = true;
  if (status) status.textContent = "正在上传并解析模板...";
  try {
    const result = await api("/api/v1/bid-templates/upload", { method: "POST", body: payload });
    state.bidTemplates = result.templates || [];
    state.selectedBidTemplateId = result.template?.template_id || state.selectedBidTemplateId;
    renderBidTemplates();
    renderBidTemplateDetail();
    await refreshBidTemplateRecommendation();
    form.reset();
    if (status) status.textContent = "模板只会入库预览，不会覆盖项目内容。";
    closeBidTemplateUploadDialog();
    toast(result.message || "模板已上传");
  } catch (error) {
    if (status) status.textContent = userFacingErrorMessage(error);
    toast(`模板上传失败：${userFacingErrorMessage(error)}`);
  } finally {
    submit.disabled = false;
  }
}

async function migrateExcellentBidLibrary() {
  const button = $("#migrate-library");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "同步中";
  try {
    const manifest = await api("/api/v1/rag/sources/migrate", { method: "POST" });
    state.excellentBidLibrary = manifest;
    renderExcellentBidLibrary(manifest);
    toast("历史资料索引已同步");
  } catch (error) {
    toast(`同步失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

function openExcellentBidUploadDialog() {
  const dialog = $("#excellent-bid-upload-dialog");
  $("#excellent-bid-upload-status").textContent = "";
  $("#excellent-bid-upload-form").reset();
  const allowReuse = $("#excellent-bid-upload-form").elements.allow_image_reuse;
  if (allowReuse) allowReuse.checked = true;
  dialog.showModal();
}

function closeExcellentBidUploadDialog() {
  $("#excellent-bid-upload-dialog").close();
}

async function uploadExcellentBid(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const status = $("#excellent-bid-upload-status");
  const submit = form.querySelector('button[type="submit"]');
  const formData = new FormData(form);
  if (!formData.get("project_type")) {
    toast("请选择项目类型");
    return;
  }
  if (formData.get("desensitized_confirmed") !== "on") {
    toast("请先确认文件已完成脱敏");
    return;
  }
  formData.set("allow_image_reuse", formData.get("allow_image_reuse") === "on" ? "true" : "false");
  formData.set("desensitized_confirmed", "true");
  if (!formData.get("knowledge_type")) {
    formData.set("knowledge_type", "excellent_bid");
  }
  submit.disabled = true;
  status.textContent = "正在上传并创建解析任务";
  try {
    const result = await api("/api/v1/rag/sources/upload", {
      method: "POST",
      body: formData,
    });
    toast("参考资料已上传，正在自动解析入库");
    status.textContent = "解析任务已创建";
    closeExcellentBidUploadDialog();
    await refreshExcellentBidLibrary();
    if (result.job?.job_id) {
      pollExcellentBidIngestionJob(result.job.job_id);
    }
  } catch (error) {
    status.textContent = "上传失败";
    toast(`上传失败：${error.message}`);
  } finally {
    submit.disabled = false;
  }
}

async function pollExcellentBidIngestionJob(jobId, attempt = 0) {
  if (attempt > 180) return;
  try {
    const job = await api(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    if (jobStatus(job) === "succeeded") {
      toast("参考资料解析入库完成");
      await refreshExcellentBidLibrary();
      return;
    }
    if (job.is_terminal && jobStatus(job) !== "succeeded") {
      toast(`参考资料解析失败：${job.error?.message || job.error_message || job.message || "请查看后台日志"}`);
      await refreshExcellentBidLibrary();
      return;
    }
    window.setTimeout(() => pollExcellentBidIngestionJob(jobId, attempt + 1), 2000);
  } catch (error) {
    if (attempt < 3) {
      window.setTimeout(() => pollExcellentBidIngestionJob(jobId, attempt + 1), 2000);
    }
  }
}

function renderExcellentBidLibrary(manifest) {
  $("#library-source-count").textContent = fmtNumber(manifest.source_count);
  $("#library-slice-count").textContent = fmtNumber(manifest.slice_count);
  $("#library-table-count").textContent = fmtNumber(manifest.table_count);
  $("#library-image-count").textContent = fmtNumber(manifest.image_count);
  $("#library-warning-count").textContent = fmtNumber(manifest.warning_count);
  renderLibraryQualityPanel(manifest.quality_summary || {});
  renderLibraryTypeBadges(manifest.sources || []);
  $("#library-status").textContent = manifest.status === "ready" ? "已同步" : "暂无正式入库记录";
  $("#library-meta").textContent = manifest.migration_source
    ? `来源索引：${manifest.migration_source} · 生成时间：${fmtTime(manifest.generated_at)}`
    : "未发现可迁移的历史资料索引。";

  const list = $("#library-list");
  list.innerHTML = "";
  renderMaterialSearchSources(manifest.sources || []);
  const sources = manifest.sources || [];
  if (!sources.length) {
    list.innerHTML = '<div class="mini-empty">暂无参考资料记录。可先点击“同步历史索引”，或后续上传参考资料后入库。</div>';
    $("#library-detail").innerHTML = '<div class="mini-empty">暂无可查看的参考资料。</div>';
    return;
  }
  if (!state.selectedLibrarySourceId || !sources.some((source) => source.source_bid_id === state.selectedLibrarySourceId)) {
    state.selectedLibrarySourceId = sources[0].source_bid_id;
  }
  for (const source of sources) {
    const item = document.createElement("article");
    item.className = `library-card${source.source_bid_id === state.selectedLibrarySourceId ? " active" : ""}`;
    const warnings = (source.warnings || [])
      .map((warning) => `<li>${escapeHtml(warning)}</li>`)
      .join("");
    const files = (source.original_file_names || [])
      .map((fileName) => `<span>${escapeHtml(fileName)}</span>`)
      .join("");
    item.innerHTML = `
      <div class="library-card-head">
        <div>
          <h2>${escapeHtml(source.title)}</h2>
          <p>${escapeHtml(source.source_type_label || source.source_type || "参考资料")} · ${escapeHtml(source.status_label || source.status || "已入库")}</p>
        </div>
        <div class="library-card-actions">
          <span class="library-badge ${escapeHtml(source.quality_level || "ready")}">${escapeHtml(qualityLevelLabel(source.quality_level))}</span>
          ${isAdmin() ? `<button type="button" class="danger-button small-button" data-delete-library-source="${escapeHtml(source.source_bid_id)}">删除</button>` : ""}
        </div>
      </div>
      <div class="library-tags">
        <span class="library-tag">${escapeHtml(source.project_type_label || "未标记项目类型")}</span>
        <span class="library-tag">${escapeHtml(source.knowledge_type_label || fmtKnowledgeType(source.knowledge_type))}</span>
        <span class="library-tag">${escapeHtml(source.bid_type_label || "施工技术标")}</span>
        <span class="library-tag">${source.allow_image_reuse === false ? "图片不复用" : "图片可复用"}</span>
        <span class="library-tag">${source.desensitized_confirmed === false ? "待脱敏确认" : "已脱敏确认"}</span>
        ${(source.quality_flags || []).slice(0, 3).map((flag) => `<span class="library-tag quality">${escapeHtml(flag)}</span>`).join("")}
      </div>
      <p class="library-usage-advice">${escapeHtml(source.usage_advice || "可作为参考资料，使用前建议核对来源和适用范围。")}</p>
      <div class="library-stats">
        <span><strong>${fmtNumber(source.slice_count)}</strong>章节切片</span>
        <span><strong>${fmtNumber(source.table_count)}</strong>表格</span>
        <span><strong>${fmtNumber(source.image_count)}</strong>图片</span>
        <span><strong>${fmtNumber(source.unmatched_count)}</strong>未匹配</span>
      </div>
      <div class="library-files">${files || "<span>未记录原始文件</span>"}</div>
      ${warnings ? `<ul class="library-warnings">${warnings}</ul>` : ""}
    `;
    item.querySelector("[data-delete-library-source]")?.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteLibrarySource(source.source_bid_id, source.title);
    });
    item.addEventListener("click", () => selectLibrarySource(source.source_bid_id));
    list.appendChild(item);
  }
  if (state.selectedLibrarySourceId) {
    refreshExcellentBidDetail(state.selectedLibrarySourceId);
  }
}

function renderLibraryQualityPanel(summary) {
  const panel = $("#library-quality-panel");
  if (!panel) return;
  const score = Number(summary.readiness_score || 0);
  const counts = summary.knowledge_type_counts || {};
  const typeLine = Object.entries(counts)
    .slice(0, 4)
    .map(([label, count]) => `${label} ${count}`)
    .join(" · ");
  panel.className = `library-quality-panel ${escapeHtml(summary.level || "empty")}`;
  panel.innerHTML = `
    <div>
      <span class="ai-kicker">资料库健康度</span>
      <strong>${escapeHtml(summary.label || "待入库")} · ${score}%</strong>
      <p>${escapeHtml(summary.advice || "建议先上传已脱敏的参考资料。")}</p>
      ${typeLine ? `<small>${escapeHtml(typeLine)}</small>` : ""}
    </div>
    <div class="library-quality-grid">
      <span><strong>${fmtNumber(summary.ready_source_count || 0)}</strong>可用</span>
      <span><strong>${fmtNumber(summary.pending_review_count || 0)}</strong>待复核</span>
      <span><strong>${fmtNumber(summary.risk_source_count || 0)}</strong>风险</span>
      <span><strong>${fmtNumber(summary.desensitized_count || 0)}</strong>已脱敏</span>
    </div>
  `;
}

function renderLibraryTypeBadges(sources = []) {
  const target = $("#library-type-badges");
  if (!target) return;
  const counts = new Map();
  for (const source of sources) {
    const key = source?.knowledge_type || "other";
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  target.innerHTML = RAG_KNOWLEDGE_TYPE_ORDER.map((type) => {
    const count = counts.get(type) || 0;
    return `
      <span class="knowledge-type-badge${count ? " active" : ""}">
        <strong>${escapeHtml(fmtKnowledgeType(type))}</strong>
        <i>${fmtNumber(count)}</i>
      </span>
    `;
  }).join("");
}

function qualityLevelLabel(level) {
  const map = {
    ready: "可用",
    review: "待复核",
    risk: "有风险",
    blocked: "不可用",
    empty: "待入库",
  };
  return map[level] || "可用";
}

function renderMaterialSearchSources(sources) {
  const select = $("#material-search-source");
  const current = select.value;
  select.innerHTML = '<option value="">全部依据资料</option>';
  for (const source of sources) {
    const option = document.createElement("option");
    option.value = source.source_bid_id;
    option.textContent = `${source.knowledge_type_label || fmtKnowledgeType(source.knowledge_type)} · ${source.title}`;
    option.selected = current === source.source_bid_id;
    select.appendChild(option);
  }
}

async function selectLibrarySource(sourceBidId) {
  state.selectedLibrarySourceId = sourceBidId;
  renderExcellentBidLibrary(state.excellentBidLibrary);
  await refreshExcellentBidDetail(sourceBidId);
}

async function deleteLibrarySource(sourceBidId, title = "") {
  if (!sourceBidId) return;
  const confirmed = window.confirm(
    `确认从投标知识库移除这份资料？\n${title || sourceBidId}\n\n该操作会从知识库列表和检索索引中移除记录，不会自动删除原始业务文件。`
  );
  if (!confirmed) return;
  try {
    await api(`/api/v1/rag/sources/${encodeURIComponent(sourceBidId)}/delete`, { method: "POST" });
    if (state.selectedLibrarySourceId === sourceBidId) {
      state.selectedLibrarySourceId = null;
    }
    state.ragMaterials = null;
    state.ragMaterialsQueryKey = "";
    toast("智库资料已移除");
    await refreshExcellentBidLibrary();
    if (state.selectedProjectId) {
      await refreshAiContext({ silent: true });
    }
  } catch (error) {
    try {
      await api(`/api/v1/rag/sources/${encodeURIComponent(sourceBidId)}`, { method: "DELETE" });
      if (state.selectedLibrarySourceId === sourceBidId) {
        state.selectedLibrarySourceId = null;
      }
      state.ragMaterials = null;
      state.ragMaterialsQueryKey = "";
      toast("智库资料已移除");
      await refreshExcellentBidLibrary();
      if (state.selectedProjectId) {
        await refreshAiContext({ silent: true });
      }
    } catch (fallbackError) {
      toast(`删除智库资料失败：${fallbackError.message || error.message}`);
    }
  }
}

async function refreshExcellentBidDetail(sourceBidId) {
  try {
    const detail = await api(`/api/v1/rag/sources/${encodeURIComponent(sourceBidId)}?limit=20`);
    renderExcellentBidDetail(detail);
  } catch (error) {
    toast(`素材详情加载失败：${error.message}`);
  }
}

function renderExcellentBidDetail(detail) {
  const source = detail.source;
  $("#library-detail-title").textContent = source.title;
  const warnings = (source.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  const slices = (detail.slice_preview || []).map(renderSlicePreview).join("");
  $("#library-detail").innerHTML = `
    <div class="library-source-advice ${escapeHtml(source.quality_level || "ready")}">
      <strong>${escapeHtml(qualityLevelLabel(source.quality_level))}</strong>
      <span>${escapeHtml(source.usage_advice || "可作为参考资料，使用前建议核对来源和适用范围。")}</span>
    </div>
    <div class="detail-summary">
      <span><strong>${escapeHtml(source.knowledge_type_label || fmtKnowledgeType(source.knowledge_type))}</strong>资料类型</span>
      <span><strong>${fmtNumber(source.slice_count)}</strong>章节切片</span>
      <span><strong>${fmtNumber(source.table_count)}</strong>表格</span>
      <span><strong>${fmtNumber(source.image_count)}</strong>图片</span>
      <span><strong>${fmtNumber(source.unmatched_count)}</strong>未匹配</span>
    </div>
    ${(source.quality_flags || []).length ? `<div class="library-tags">${(source.quality_flags || []).map((flag) => `<span class="library-tag quality">${escapeHtml(flag)}</span>`).join("")}</div>` : ""}
    ${warnings ? `<ul class="library-warnings">${warnings}</ul>` : ""}
    <div class="slice-preview-list">${slices || '<div class="mini-empty">暂无章节切片预览。</div>'}</div>
  `;
}

function renderSlicePreview(item) {
  const path = (item.section_path || []).join(" / ");
  return `
    <article class="slice-item">
      <div>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(path || "未记录章节路径")}</small>
      </div>
      <div class="slice-meta">
        <span>${fmtNumber(item.table_count)} 表</span>
        <span>${fmtNumber(item.image_count)} 图</span>
        <span>${fmtNumber(item.paragraph_char_count)} 字</span>
      </div>
      ${item.text_preview ? `<p>${escapeHtml(item.text_preview)}</p>` : ""}
    </article>
  `;
}

async function searchMaterials(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const query = String(form.get("query") || "").trim();
  const sourceBidId = String(form.get("source_bid_id") || "");
  const params = new URLSearchParams({ q: query, limit: "30" });
  if (sourceBidId) params.set("source_bid_id", sourceBidId);
  $("#material-search-status").textContent = "检索中";
  try {
    const result = await api(`/api/v1/rag/sources/search?${params.toString()}`);
    renderMaterialSearchResults(result);
  } catch (error) {
    toast(`素材检索失败：${error.message}`);
  } finally {
    $("#material-search-status").textContent = "";
  }
}

function renderMaterialSearchResults(result) {
  const list = $("#material-search-results");
  const results = result.results || [];
  $("#material-search-status").textContent = `共 ${fmtNumber(result.total)} 条结果`;
  if (!results.length) {
    list.innerHTML = '<div class="mini-empty">没有匹配的章节素材。可以换一个施工工序或管理主题关键词。</div>';
    return;
  }
  list.innerHTML = results
    .map(
      (item) => `
        <article class="material-result">
          <div class="material-result-head">
            <strong>${escapeHtml(item.title)}</strong>
            <span>${escapeHtml(item.knowledge_type_label || fmtKnowledgeType(item.knowledge_type))} · ${escapeHtml(item.source_title || "")}</span>
          </div>
          <small>${escapeHtml((item.section_path || []).join(" / "))}</small>
          <div class="slice-meta">
            <span>${fmtNumber(item.table_count)} 表</span>
            <span>${fmtNumber(item.image_count)} 图</span>
            <span>${fmtNumber(item.paragraph_char_count)} 字</span>
          </div>
          ${item.text_preview ? `<p>${escapeHtml(item.text_preview)}</p>` : ""}
        </article>
      `
    )
    .join("");
}

function renderProfiles(tasks) {
  const list = $("#profiles-list");
  list.innerHTML = "";
  if (!Object.keys(tasks).length) {
    list.innerHTML = '<div class="mini-empty">暂未读取到任务参数方案。</div>';
    return;
  }
  for (const [key, profile] of Object.entries(tasks)) {
    const item = document.createElement("fieldset");
    item.className = "profile-item profile-editor";
    item.dataset.profileKey = key;
    item.innerHTML = `
      <legend>${escapeHtml(fmtProfileName(key))}</legend>
      <span class="muted">${escapeHtml(key)}</span>
      <div class="profile-fields">
        <label>最大输出 <input name="max_tokens" type="number" min="1" value="${escapeHtml(profileFieldValue(profile, "max_tokens"))}" /></label>
        <label>超时秒数 <input name="timeout_seconds" type="number" min="1" value="${escapeHtml(profileFieldValue(profile, "timeout_seconds"))}" /></label>
        <label>并发数 <input name="max_workers" type="number" min="1" max="12" value="${escapeHtml(profileFieldValue(profile, "max_workers"))}" /></label>
        <label>温度 <input name="temperature" type="number" step="0.01" value="${escapeHtml(profileFieldValue(profile, "temperature"))}" /></label>
        <label>Top P <input name="top_p" type="number" step="0.01" value="${escapeHtml(profileFieldValue(profile, "top_p"))}" /></label>
        <label>
          结构化输出
          <select name="structured_output_type">
            <option value="" ${profileFieldValue(profile, "structured_output_type") ? "" : "selected"}>不指定</option>
            <option value="json_object" ${profileFieldValue(profile, "structured_output_type") === "json_object" ? "selected" : ""}>JSON 对象</option>
          </select>
        </label>
      </div>
      <label class="check-line compact-check"><input name="enable_thinking" type="checkbox" ${profileChecked(profile, "enable_thinking") ? "checked" : ""} />启用思考模式</label>
    `;
    list.appendChild(item);
  }
  renderGenerationConcurrencyHint(tasks.technical_bid_chapter_generation || {});
}

function renderGenerationConcurrencyHint(profile) {
  const hint = $("#generation-run-hint");
  if (!hint) return;
  const configured = Number(profile.max_workers || state.modelConfig?.effective_default?.max_workers || 1);
  const workers = Number.isFinite(configured) && configured > 0 ? configured : 1;
  hint.textContent = `当前真实正文生成并发：配置值 ${workers}。后端会按待生成章节数量动态取值，实际并发不超过待生成数量。`;
}

function collectTaskProfiles() {
  const tasks = {};
  $$("#profiles-list .profile-editor").forEach((fieldset) => {
    const key = fieldset.dataset.profileKey;
    const fieldValue = (name) => fieldset.querySelector(`[name="${name}"]`)?.value ?? "";
    const numberOrNull = (name) => {
      const value = fieldValue(name);
      return value === "" ? null : Number(value);
    };
    const structured = fieldValue("structured_output_type");
    tasks[key] = {
      max_tokens: numberOrNull("max_tokens"),
      timeout_seconds: numberOrNull("timeout_seconds"),
      max_workers: numberOrNull("max_workers"),
      temperature: numberOrNull("temperature"),
      top_p: numberOrNull("top_p"),
      structured_output_type: structured || null,
      enable_thinking: Boolean(fieldset.querySelector('[name="enable_thinking"]')?.checked),
      reasoning_effort: state.modelConfig?.tasks?.[key]?.reasoning_effort ?? "none",
    };
  });
  return tasks;
}

async function saveModelConfig(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const numberOrNull = (name) => {
    const value = form[name].value;
    return value === "" ? null : Number(value);
  };
  const payload = {
    provider: form.provider.value,
    api_type: form.api_type.value,
    base_url: form.base_url.value,
    model: form.model.value,
    api_key: form.api_key.value || null,
    temperature: numberOrNull("temperature"),
    top_p: numberOrNull("top_p"),
    timeout_seconds: numberOrNull("timeout_seconds"),
    max_retries: numberOrNull("max_retries"),
    max_workers: numberOrNull("max_workers"),
    structured_output_type: form.structured_output_type.value || null,
    enable_thinking: form.enable_thinking.checked,
    default_profile: state.modelConfig?.default_profile || null,
    tasks: collectTaskProfiles(),
  };
  try {
    await api("/api/v1/model-config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    form.api_key.value = "";
    toast("AI 服务设置已保存");
    await refreshModelConfig();
  } catch (error) {
    toast(`保存失败：${userFacingErrorMessage(error)}`);
  }
}

function bindNavigation() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      switchView(button.dataset.view || "projects");
    });
  });
}

function toggleAssistantDock(forceOpen = null) {
  const dock = $("#assistant-dock");
  const shouldOpen = forceOpen === null ? dock.classList.contains("hidden") : Boolean(forceOpen);
  dock.classList.toggle("hidden", !shouldOpen);
  document.body.classList.toggle("assistant-dock-open", shouldOpen);
  $("#assistant-float-button")?.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
  if (shouldOpen) {
    refreshProjectContext({
      silent: true,
      refreshFiles: false,
      refreshWorkflow: true,
      refreshAssistant: true,
    });
  }
}

function bindEvents() {
  $("#auth-login-tab")?.addEventListener("click", () => setAuthMode("login"));
  $("#auth-register-tab")?.addEventListener("click", () => setAuthMode("register"));
  $("#login-form")?.addEventListener("submit", login);
  $("#register-form")?.addEventListener("submit", register);
  $("#logout-button")?.addEventListener("click", logout);
  $("#refresh-projects").addEventListener("click", refreshProjects);
  $("#top-refresh")?.addEventListener("click", () => {
    if (["home", "qa", "bid-intel", "risk", "suppliers"].includes(state.activeView)) {
      refreshHealth();
      toast("平台状态已刷新");
      return null;
    }
    if (state.activeView === "rag") return refreshExcellentBidLibrary();
    if (state.activeView === "templates") return refreshBidTemplates();
    if (state.activeView === "model") return refreshModelConfig();
    if (state.activeView === "accounts") return isAdmin() ? refreshAccounts() : null;
    return state.selectedProjectId ? refreshProjectData() : refreshProjects();
  });
  $("#delete-project").addEventListener("click", deleteCurrentProject);
  $("#create-project-toggle").addEventListener("click", toggleCreateProjectForm);
  $("#project-form").addEventListener("submit", createProject);
  $("#project-select").addEventListener("change", (event) => selectProject(event.target.value));
  $("#upload-form").addEventListener("submit", uploadFile);
  $("#assistant-float-button")?.addEventListener("click", () => toggleAssistantDock());
  $("#assistant-close")?.addEventListener("click", () => toggleAssistantDock(false));
  $("#refresh-ai-assistant")?.addEventListener("click", () =>
    refreshProjectContext({
      silent: false,
      refreshFiles: false,
      refreshWorkflow: true,
      refreshAssistant: true,
    })
  );
  $("#ai-open-assistant")?.addEventListener("click", () => toggleAssistantDock(true));
  $("#ai-go-next-step")?.addEventListener("click", (event) => setActiveStep(event.currentTarget.dataset.targetStep || "upload"));
  $("#assistant-chat-form")?.addEventListener("submit", askAssistant);
  renderAssistantQuickActions();
  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => setActiveStep(button.dataset.step));
  });
  $$("[data-job]").forEach((button) => {
    button.addEventListener("click", () => createJob(button.dataset.job, button));
  });
  $("#confirm-visible-scores")?.addEventListener("click", confirmAllScorePoints);
  $("#outline-preview")?.addEventListener("click", handleOutlineAction);
  $("#expand-outline")?.addEventListener("click", () => setOutlineCollapsedAll(false));
  $("#collapse-outline")?.addEventListener("click", () => setOutlineCollapsedAll(true));
  $("#save-outline")?.addEventListener("click", saveOutlineAdjustments);
  $("#select-pending-generation-units")?.addEventListener("click", selectPendingGenerationUnits);
  $("#select-failed-generation-units")?.addEventListener("click", selectFailedGenerationUnits);
  $("#clear-generation-units")?.addEventListener("click", clearGenerationUnits);
  $("#generate-selected-chapters")?.addEventListener("click", (event) => createChapterLlmJob({ runAll: false, button: event.currentTarget }));
  $("#continue-unfinished-chapters")?.addEventListener("click", (event) => generateUnfinishedChapters(event.currentTarget));
  $("#retry-failed-chapters")?.addEventListener("click", (event) => retryFailedChapters(event.currentTarget));
  $("#refresh-word-draft")?.addEventListener("click", (event) => refreshWordDraft(event.currentTarget));
  $("#go-word-review")?.addEventListener("click", () => setActiveStep("review"));
  $("#word-refresh-summary")?.addEventListener("click", () => refreshWordExportPage());
  $("#word-open-onlyoffice")?.addEventListener("click", () => loadOnlyOfficePreview());
  $("#word-save-onlyoffice")?.addEventListener("click", (event) => saveOnlyOfficeDocument(event.currentTarget));
  $("#word-fullscreen-onlyoffice")?.addEventListener("click", (event) => fullscreenOnlyOfficePreview(event.currentTarget));
  $("#word-open-onlyoffice-toc")?.addEventListener("click", (event) => openTocPageNumberHelp(event.currentTarget));
  $("#word-toc-page-help")?.addEventListener("click", (event) => openTocPageNumberHelp(event.currentTarget));
  $("#word-reexport")?.addEventListener("click", (event) => reexportWord(event.currentTarget));
  $("#word-toggle-review-drawer")?.addEventListener("click", () => setWordReviewDrawer(true));
  $("#word-close-review-drawer")?.addEventListener("click", () => setWordReviewDrawer(false));
  $("#word-drawer-backdrop")?.addEventListener("click", () => setWordReviewDrawer(false));
  $("#word-profile-form")?.addEventListener("submit", saveWordProfile);
  $("#word-profile-reset")?.addEventListener("click", (event) => resetWordProfile(event.currentTarget));
  $("#refresh-model").addEventListener("click", refreshModelConfig);
  $("#model-form").addEventListener("submit", saveModelConfig);
  $("#open-system-settings")?.addEventListener("click", () => switchView("model"));
  $("#open-account-settings")?.addEventListener("click", () => switchView("accounts"));
  $("#back-system-settings")?.addEventListener("click", () => switchView("model"));
  $("#open-excellent-bid-upload")?.addEventListener("click", openExcellentBidUploadDialog);
  $("#close-excellent-bid-upload")?.addEventListener("click", closeExcellentBidUploadDialog);
  $("#cancel-excellent-bid-upload")?.addEventListener("click", closeExcellentBidUploadDialog);
  $("#excellent-bid-upload-form")?.addEventListener("submit", uploadExcellentBid);
  $("#refresh-library").addEventListener("click", refreshExcellentBidLibrary);
  $("#migrate-library").addEventListener("click", migrateExcellentBidLibrary);
  $("#material-search-form").addEventListener("submit", searchMaterials);
  $("#refresh-bid-templates")?.addEventListener("click", refreshBidTemplates);
  $("#bid-template-filter-form")?.addEventListener("input", handleBidTemplateFilters);
  $("#bid-template-filter-form")?.addEventListener("change", handleBidTemplateFilters);
  $("#open-bid-template-upload")?.addEventListener("click", openBidTemplateUploadDialog);
  $("#close-bid-template-upload")?.addEventListener("click", closeBidTemplateUploadDialog);
  $("#cancel-bid-template-upload")?.addEventListener("click", closeBidTemplateUploadDialog);
  $("#bid-template-upload-form")?.addEventListener("submit", uploadBidTemplate);
  $("#refresh-accounts")?.addEventListener("click", refreshAccounts);
  $("#account-form")?.addEventListener("submit", createAccount);
}

async function loadWorkbenchData() {
  await refreshHealth();
  await refreshProjects();
  await refreshModelConfig();
  await refreshExcellentBidLibrary();
  await refreshBidTemplates();
  if (isAdmin()) {
    await refreshAccounts();
  }
}

async function init() {
  bindEvents();
  applySidebarState();
  switchView(state.activeView);
  setActiveStep(state.activeStep, { userInitiated: false, refreshStepData: false });
  const authenticated = await refreshCurrentAccount();
  await refreshHealth();
  if (authenticated) {
    await loadWorkbenchData();
  }
}

init();

