const WORKSPACE_KEY = "resume-agent.current-workspace";
import { createResumeWorkspace } from "./resume-workspace.js?v=20260821-contextual-workbench-title";
import { createJobMatching } from "./job-matching.js?v=20260821-job-rail-overview";
import { updateWorkbenchContext } from "../workbench-context.js";
import { createOperationMonitor } from "./operation-monitor.js";
import { createApplicationsBoard } from "./applications-board.js";

export function createWorkbenchShell({ getApiBase, elements }) {
  let workspaces = [];
  let activeWorkspaceId = localStorage.getItem(WORKSPACE_KEY) || "";
  let loading = false;
  let activeRoute = "workbench";
  let contextWorkspaceId = "";
  let currentHome = null;

  function setContentHeadingVisibility(panel) {
    const heading = elements.title.closest(".workbench-section-heading");
    const hasResume = Boolean(currentHome?.recent_versions?.[0]);
    // 档案页已有完整的纸张预览标题，不再重复显示“当前简历档案”。
    heading.hidden = panel !== "match" && hasResume;
  }

  function showContentPanel(panel) {
    const archive = panel !== "match";
    elements.main.hidden = !archive;
    elements.match.hidden = archive;
    elements.archiveTab.setAttribute("aria-current", archive ? "page" : "false");
    elements.matchTab.setAttribute("aria-current", archive ? "false" : "page");
    elements.mode.textContent = archive ? "档案" : "匹配分数";
    setContentHeadingVisibility(panel);
    if (archive) {
      elements.title.textContent = currentHome?.recent_versions?.[0] ? "当前简历档案" : "建立你的第一份简历档案";
    } else {
      elements.title.textContent = "准备匹配评估";
    }
  }

  function setStatus(message, error = false) {
    elements.status.textContent = message;
    elements.status.style.color = error ? "var(--wb-danger)" : "var(--wb-muted)";
  }

  async function request(path, options = {}) {
    const response = await fetch(`${getApiBase()}${path}`, options);
    let payload = null;
    try { payload = await response.json(); } catch (_error) { /* empty body */ }
    if (!response.ok) {
      const validationDetail = Array.isArray(payload?.detail)
        ? payload.detail.map(item => `${(item.loc || []).slice(1).join(".") || "请求"}：${item.msg || "无效"}`).join("；")
        : null;
      const error = new Error(payload?.error?.message || payload?.detail?.message || validationDetail || `HTTP ${response.status}`);
      error.status = response.status;
      error.code = payload?.error?.code || payload?.detail?.code || (validationDetail ? "request_validation_failed" : null);
      error.retryable = Boolean(payload?.error?.retryable);
      throw error;
    }
    return payload;
  }

  const resumeWorkspace = createResumeWorkspace({
    request,
    apiBase: getApiBase,
    elements: {
      main: elements.main,
      resumes: elements.resumeList,
      jobs: elements.jobList,
      status: elements.status,
    },
    reloadHome: () => load(true),
  });
  const jobMatching = createJobMatching({
    request,
    elements: { main: elements.match, jobs: elements.jobList, status: elements.status },
    activatePanel: () => showContentPanel("match"),
    reloadHome: () => load(true),
  });
  const operationMonitor = createOperationMonitor({ request, apiBase: getApiBase, container: elements.operationCards });
  const applicationsBoard = createApplicationsBoard({ request, elements: { main: elements.main } });

  function setStepStates(stats, mode) {
    const states = {
      resume: stats.resume_count > 0 ? "done" : "current",
      job: stats.resume_count > 0 && stats.job_count > 0 ? "done" : stats.resume_count > 0 ? "current" : "pending",
      analysis: mode === "C" || mode === "D" ? "done" : stats.job_count > 0 ? "current" : "pending",
      revision: mode === "D" ? "current" : "pending",
      export: "pending",
    };
    // 流程条已移除；保留状态计算，供后续状态提示或埋点复用。
    return states;
  }

  function renderHome(home) {
    currentHome = home;
    const stats = home.stats || {};
    const mode = !stats.resume_count ? "A" : !stats.job_count ? "B" : "C";
    const copy = {
      A: ["建立你的第一份简历档案", "导入 DOCX 或 PDF；首次导入会自动创建本地求职档案。"],
      B: ["选择目标岗位", "简历已准备好。粘贴 JD 或输入稳定链接开始匹配。"],
      C: ["准备匹配评估", "选择已确认的简历版本和岗位快照；分析结果将展示要求项与证据。"],
      D: ["确认本次修改", "逐条审批建议后，再保存并确认新版本。"],
    }[mode];
    elements.mode.textContent = elements.match.hidden ? "档案" : "匹配分数";
    elements.title.textContent = elements.match.hidden && home.recent_versions?.[0] ? "当前简历档案" : copy[0];
    setContentHeadingVisibility(elements.match.hidden ? "archive" : "match");
    elements.main.textContent = copy[1];
    elements.jobCount.textContent = `${stats.job_count || 0} 个`;
    elements.jobList.textContent = stats.job_count
      ? `已确认岗位 ${stats.job_count} 个；候选来源需逐项确认。`
      : "暂无已确认岗位。";
    elements.agentContext.textContent = stats.resume_count
      ? `当前上下文：${home.workspace?.name || "求职目标"}；Agent 不会自动提交修改。`
      : "建立档案后，Agent 才会获得显式 ResumeVersion 上下文。";
    setStepStates(stats, mode);
    setStatus(`已加载 ${home.workspace?.name || "工作台"} · revision ${home.workspace?.revision || 0}`);
    if (home.workspace?.workspace_id) {
      const changed = contextWorkspaceId && contextWorkspaceId !== home.workspace.workspace_id;
      contextWorkspaceId = home.workspace.workspace_id;
      updateWorkbenchContext(changed
        ? { workspace_id: contextWorkspaceId, resume_version_id: null, job_snapshot_id: null, match_analysis_id: null, resume_branch_id: null, lineage_focus_version_id: null, merge_proposal_id: null }
        : { workspace_id: contextWorkspaceId });
    }
    resumeWorkspace.renderResumeList(
      home,
      activeRoute,
      home.workspace?.workspace_id || activeWorkspaceId,
    );
    if (activeRoute === "workbench") {
      jobMatching.renderJobList(home, activeWorkspaceId);
      if (home.recent_versions?.[0]) {
        showContentPanel("archive");
        void resumeWorkspace.renderResumePreview(
          activeWorkspaceId,
          home.recent_versions[0].version_id,
          home.recent_versions[0].label,
        );
      }
      operationMonitor.load(activeWorkspaceId);
    } else if (activeRoute === "applications") {
      elements.jobList.textContent = "投递记录只绑定已确认的岗位快照与简历版本。";
      applicationsBoard.render(activeWorkspaceId);
    }
  }

  function renderWorkspaceOptions() {
    elements.workspace.replaceChildren();
    if (!workspaces.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "尚未创建求职目标";
      elements.workspace.append(option);
      elements.workspace.disabled = true;
      return;
    }
    elements.workspace.disabled = false;
    for (const workspace of workspaces) {
      const option = document.createElement("option");
      option.value = workspace.workspace_id;
      option.textContent = workspace.name;
      option.selected = workspace.workspace_id === activeWorkspaceId;
      elements.workspace.append(option);
    }
  }

  async function load(force = false) {
    if (loading) return;
    loading = true;
    setStatus("正在加载权威工作台状态…");
    try {
      const page = await request("/v1/workbench/workspaces?limit=50");
      workspaces = page.items || [];
      if (!workspaces.some(item => item.workspace_id === activeWorkspaceId)) {
        activeWorkspaceId = workspaces[0]?.workspace_id || "";
      }
      renderWorkspaceOptions();
      if (!activeWorkspaceId) {
        renderHome({ stats: {}, recent_versions: [], workspace: null });
        setStatus("尚未创建求职目标；导入首份简历时会自动创建。", false);
        return;
      }
      localStorage.setItem(WORKSPACE_KEY, activeWorkspaceId);
      renderHome(await request(`/v1/workbench/workspaces/${encodeURIComponent(activeWorkspaceId)}/home`));
    } catch (error) {
      setStatus(`工作台加载失败：${error.message}`, true);
      elements.main.textContent = "数据未加载成功。已保留当前页面，可稍后重试。";
    } finally {
      loading = false;
    }
  }

  elements.workspace.addEventListener("change", () => {
    activeWorkspaceId = elements.workspace.value;
    localStorage.setItem(WORKSPACE_KEY, activeWorkspaceId);
    load();
  });
  elements.archiveTab.addEventListener("click", () => {
    showContentPanel("archive");
    const version = currentHome?.recent_versions?.[0];
    if (version) void resumeWorkspace.renderResumePreview(activeWorkspaceId, version.version_id, version.label);
  });
  elements.matchTab.addEventListener("click", () => {
    showContentPanel("match");
    if (currentHome) jobMatching.renderMain(currentHome, activeWorkspaceId);
  });
  async function activate(route) {
    activeRoute = route;
    elements.title.textContent = route === "version-map" ? "版本地图" : route === "applications" ? "投递看板" : elements.title.textContent;
    await load(true);
  }
  return Object.freeze({ activate, load });
}
