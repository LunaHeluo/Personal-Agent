const TERMINAL = new Set(["committed", "failed", "rejected", "cancelled"]);

export function createOperationMonitor({ request, apiBase, container }) {
  const streams = new Map();

  function stop(operationId) {
    const state = streams.get(operationId);
    if (state?.timer) window.clearTimeout(state.timer);
    state?.controller?.abort();
    streams.delete(operationId);
  }

  async function load(workspaceId) {
    for (const key of streams.keys()) stop(key);
    container.replaceChildren();
    if (!workspaceId) return;
    try {
      const page = await request(`/v1/workbench/operations?workspace_id=${encodeURIComponent(workspaceId)}&limit=50`);
      const operations = [...(page.items || [])].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, 8);
      if (!operations.length) { container.textContent = "当前没有业务任务。"; return; }
      for (const operation of operations) renderCard(operation);
    } catch (error) { container.textContent = `任务加载失败：${error.message}`; }
  }

  function stageText(operation) {
    const labels = {
      created: "已创建，等待执行", running: "执行中", partial: "部分结果待验证",
      waiting_for_user: "等待用户决定", validating: "Run 已完成，正在验证业务结果",
      committing: "验证通过，正在提交业务对象", committed: "业务已提交",
      commit_failed: "执行成功，但业务提交失败", rejected: "验证未通过",
      failed: "执行失败", cancelled: "已取消",
    };
    return labels[operation.status] || operation.status;
  }

  function renderCard(operation) {
    const card = document.createElement("article"); card.className = "operation-card"; card.dataset.operationId = operation.operation_id; card.dataset.status = operation.status;
    const heading = document.createElement("div"); heading.className = "operation-card-heading";
    const title = document.createElement("strong"); title.textContent = operation.operation_type;
    const status = document.createElement("span"); status.className = "status-label"; status.textContent = operation.status; heading.append(title, status);
    const phase = document.createElement("p"); phase.textContent = stageText(operation);
    const meta = document.createElement("small"); meta.textContent = `${operation.operation_id} · revision ${operation.revision} · 更新 ${new Date(operation.updated_at).toLocaleString()}`;
    const eventList = document.createElement("ol"); eventList.className = "operation-events";
    const details = document.createElement("details"); const summary = document.createElement("summary"); summary.textContent = "高级详情"; const advanced = document.createElement("pre"); advanced.textContent = JSON.stringify({ parent_run_id: operation.parent_run_id, task_id: operation.task_id, error_code: operation.error_code, retryable: operation.retryable, result_object_id: operation.result_object_id }, null, 2); details.append(summary, advanced);
    card.append(heading, phase, meta, eventList, details); container.append(card);
    if (operation.status === "waiting_for_user") { const reason = document.createElement("p"); reason.className = "operation-waiting"; reason.textContent = `需要用户决定：${operation.error_code || "请打开对应业务对象查看安全继续动作。"}`; card.append(reason); }
    if (operation.status === "commit_failed") { const warning = document.createElement("p"); warning.className = "operation-error"; warning.textContent = "Run 成功不等于业务成功。请通过所属资源命令重试提交。"; card.append(warning); }
    if (operation.parent_run_id && !TERMINAL.has(operation.status)) monitorRun(operation, { card, eventList, advanced, lastSeq: 0, seen: new Set(), attempts: 0 });
  }

  async function monitorRun(operation, state) {
    stop(operation.operation_id);
    streams.set(operation.operation_id, state);
    try {
      const detailResponse = await fetch(`${apiBase()}/v1/runs/${encodeURIComponent(operation.parent_run_id)}`);
      if (!detailResponse.ok) throw new Error(`Run HTTP ${detailResponse.status}`);
      const detail = await detailResponse.json();
      state.advanced.textContent = JSON.stringify({ parent: detail.parent, child_tasks: detail.child_tasks, child_runs: detail.child_runs, orchestration: detail.orchestration }, null, 2);
      if (!["succeeded", "failed", "cancelled", "timed_out", "budget_exhausted"].includes(detail.parent?.status) && !state.card.querySelector(".operation-cancel")) {
        const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "operation-cancel"; cancel.textContent = "取消执行";
        cancel.addEventListener("click", async () => {
          cancel.disabled = true;
          const response = await fetch(`${apiBase()}/v1/runs/${encodeURIComponent(operation.parent_run_id)}/cancel`, {
            method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": `cancel-${operation.operation_id}` },
            body: JSON.stringify({ expected_version: detail.parent.version, idempotency_key: `cancel-${operation.operation_id}`, reason: "用户从简历工作台取消" }),
          });
          if (response.ok) { state.card.querySelector("p").textContent = "取消请求已确认；不会再启动新的模型或 Tool 调用。"; stop(operation.operation_id); }
          else { cancel.disabled = false; state.card.querySelector("p").textContent = `取消失败：HTTP ${response.status}`; }
        });
        state.card.append(cancel);
      }
      const response = await fetch(`${apiBase()}/v1/runs/${encodeURIComponent(operation.parent_run_id)}/events?after_seq=${state.lastSeq}&limit=500`);
      if (!response.ok) throw new Error(`Event HTTP ${response.status}`);
      const page = await response.json();
      for (const event of page.events || []) {
        if (state.seen.has(event.event_seq) || event.event_seq <= state.lastSeq) continue;
        state.seen.add(event.event_seq); state.lastSeq = Math.max(state.lastSeq, event.event_seq);
        const item = document.createElement("li"); item.textContent = `${event.event_seq} · ${event.event_type} · ${event.status || ""}`; state.eventList.append(item);
      }
      state.controller = new AbortController();
      const streamResponse = await fetch(`${apiBase()}/v1/runs/${encodeURIComponent(operation.parent_run_id)}/events/stream?after_seq=${state.lastSeq}&limit=500`, { signal: state.controller.signal });
      if (!streamResponse.ok) throw new Error(`SSE HTTP ${streamResponse.status}`);
      const streamText = await streamResponse.text();
      for (const line of streamText.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const event = JSON.parse(line.slice(5).trim());
        if (event.type !== "run_event" || state.seen.has(event.event.event_seq) || event.event.event_seq <= state.lastSeq) continue;
        state.seen.add(event.event.event_seq); state.lastSeq = Math.max(state.lastSeq, event.event.event_seq);
        const item = document.createElement("li"); item.textContent = `${event.event.event_seq} · ${event.event.event_type} · ${event.event.status || ""}`; state.eventList.append(item);
      }
      const parentStatus = detail.parent?.status;
      if (["succeeded", "failed", "cancelled", "timed_out", "budget_exhausted"].includes(parentStatus)) {
        const latest = await request(`/v1/workbench/operations/${encodeURIComponent(operation.operation_id)}`);
        state.card.dataset.status = latest.status;
        state.card.querySelector(".status-label").textContent = latest.status;
        state.card.querySelector("p").textContent = stageText(latest);
        if (TERMINAL.has(latest.status) || latest.status === "commit_failed") { stop(operation.operation_id); return; }
      }
      state.attempts = 0;
    } catch (error) {
      state.attempts += 1;
      const notice = state.card.querySelector(".operation-stream-error") || document.createElement("p"); notice.className = "operation-stream-error"; notice.textContent = `事件连接中断，将从 seq ${state.lastSeq} 恢复：${error.message}`; if (!notice.isConnected) state.card.append(notice);
    }
    const delay = Math.min(30000, 1000 * (2 ** state.attempts));
    state.timer = window.setTimeout(() => monitorRun(operation, state), delay);
    streams.set(operation.operation_id, state);
  }

  return Object.freeze({ load, stopAll: () => [...streams.keys()].forEach(stop) });
}
