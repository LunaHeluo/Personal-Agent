export function createHashRouter(routes, fallback) {
  function resolve(hash = window.location.hash) {
    const key = hash || fallback;
    return routes[key] || routes[fallback] || null;
  }
  function start(render) {
    const apply = () => render(resolve());
    window.addEventListener("hashchange", apply);
    apply();
    return () => window.removeEventListener("hashchange", apply);
  }
  return Object.freeze({ resolve, start });
}
