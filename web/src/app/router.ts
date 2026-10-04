export interface RouteState {
  debug: boolean;
  mapId: string | null;
  pointId: string | null;
  labels: string[] | null;
}

export function parseHash(hash = window.location.hash): RouteState {
  const raw = hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  const params = new URLSearchParams(query);
  const labels = params.get("labels");
  return {
    debug: path === "/debug",
    mapId: path.startsWith("/map/") ? path.slice(5) : null,
    pointId: params.get("point"),
    labels: labels ? labels.split(",").filter(Boolean) : null,
  };
}

export function writeHash(state: { mapId: string; pointId?: string | null; labels?: string[] | null }): void {
  const params = new URLSearchParams();
  if (state.pointId) params.set("point", state.pointId);
  if (state.labels && state.labels.length) params.set("labels", state.labels.join(","));
  const query = params.toString();
  const next = `#/map/${state.mapId}${query ? `?${query}` : ""}`;
  if (window.location.hash !== next) history.pushState({}, "", next);
}
