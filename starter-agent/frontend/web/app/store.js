export function createStore(initialState = {}) {
  let value = { ...initialState };
  const listeners = new Set();
  return Object.freeze({
    getState: () => value,
    setState(update) {
      value = { ...value, ...(typeof update === "function" ? update(value) : update) };
      for (const listener of listeners) listener(value);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  });
}
