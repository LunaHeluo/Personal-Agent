export function createApiClient(getBaseUrl) {
  async function request(path, options = {}) {
    const response = await fetch(`${getBaseUrl()}${path}`, options);
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const error = new Error(payload?.error?.message || payload?.detail || `HTTP ${response.status}`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }
  return Object.freeze({ request });
}
