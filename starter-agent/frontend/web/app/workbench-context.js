let current = { context_epoch: 1 };

export function updateWorkbenchContext(patch) {
  const next = { ...current, ...patch };
  const changed = Object.keys(patch).some(key => key !== "context_epoch" && patch[key] !== current[key]);
  if (changed) next.context_epoch = (current.context_epoch || 0) + 1;
  current = Object.fromEntries(Object.entries(next).filter(([, value]) => value !== null && value !== "" && value !== undefined));
  window.dispatchEvent(new CustomEvent("workbench-context-change", { detail: { ...current } }));
  return { ...current };
}

export function getWorkbenchContext() {
  return current.workspace_id ? { ...current } : null;
}
