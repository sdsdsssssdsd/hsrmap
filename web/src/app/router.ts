export interface OriginStack {
  mapIds: string[];
  pointIds: string[];
}

/** 空栈：直接导航（树 / 搜索 / 清单）不带来源。 */
export const EMPTY_ORIGINS: OriginStack = { mapIds: [], pointIds: [] };

export interface RouteState {
  debug: boolean;
  mapId: string | null;
  pointId: string | null;
  labels: string[] | null;
  origins: OriginStack;
}

function splitParam(value: string | null): string[] {
  if (!value) return [];
  return value.split(",").map((item) => item.trim());
}

/** 两个栈按位置对齐：末尾的空来源丢掉，切不让点位的下标错位。 */
function alignOrigins(mapIds: string[], pointIds: string[]): OriginStack {
  const maps = [...mapIds];
  const points = maps.map((_, index) => pointIds[index] || "");
  while (maps.length > 0 && !maps[maps.length - 1]) {
    maps.pop();
    points.pop();
  }
  return { mapIds: maps, pointIds: points };
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
    //: a1-8-1 §十二：官方把来源压进 origin_map_id 逗号栈（pushOriginMapId / topOriginMapId）；
    //: origin_point_id 是同长度的并行栈，用来精确回到来源点位。
    origins: alignOrigins(splitParam(params.get("origin_map_id")), splitParam(params.get("origin_point_id"))),
  };
}

export function pushOrigin(stack: OriginStack, mapId: string, pointId?: string | null): OriginStack {
  return { mapIds: [...stack.mapIds, mapId], pointIds: [...stack.pointIds, pointId || ""] };
}

/** 弹栈：返回栈顶（来源地图 + 来源点位）与剩下的栈。 */
export function popOrigin(stack: OriginStack): { mapId: string | null; pointId: string | null; rest: OriginStack } {
  if (stack.mapIds.length === 0) return { mapId: null, pointId: null, rest: EMPTY_ORIGINS };
  const rest = {
    mapIds: stack.mapIds.slice(0, -1),
    pointIds: stack.pointIds.slice(0, -1),
  };
  const pointId = stack.pointIds[stack.pointIds.length - 1];
  return { mapId: stack.mapIds[stack.mapIds.length - 1], pointId: pointId || null, rest };
}

export function topOrigin(stack: OriginStack): string | null {
  return stack.mapIds.length ? stack.mapIds[stack.mapIds.length - 1] : null;
}

export function writeHash(state: {
  mapId: string;
  pointId?: string | null;
  labels?: string[] | null;
  /** 缺省 = 沿用当前 URL 里的来源栈（切换标签、打开抽屉不该把返回栈抹掉）。 */
  origins?: OriginStack;
}): void {
  const params = new URLSearchParams();
  if (state.pointId) params.set("point", state.pointId);
  if (state.labels && state.labels.length) params.set("labels", state.labels.join(","));
  const origins = state.origins ?? parseHash().origins;
  if (origins.mapIds.length) {
    params.set("origin_map_id", origins.mapIds.join(","));
    params.set("origin_point_id", origins.mapIds.map((_, index) => origins.pointIds[index] || "").join(","));
  }
  const query = params.toString();
  const next = `#/map/${state.mapId}${query ? `?${query}` : ""}`;
  if (window.location.hash !== next) history.pushState({}, "", next);
}
