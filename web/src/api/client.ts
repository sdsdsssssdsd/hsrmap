import type {
  AtlasPayload,
  EvidenceOverview,
  GuideEntry,
  PointEvidencePayload,
  GreaseTopic,
  TopicPayload,
  LabelGroup,
  MapInfo,
  MapTransitionsPayload,
  PointDetail,
  PointItem,
  ProgressAtlasPayload,
  ProgressPointsPayload,
  ProgressStatus,
  SearchResult,
  SettingsInfo,
  DataSource,
  TreeNode,
  UserPoint,
} from "./types";

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${url}`);
  return response.json();
}

async function sendJson<T>(url: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${response.status} ${url}`);
  return response.json();
}

export const api = {
  tree: (refresh = false) => getJson<TreeNode[]>(`/api/v1/maps/tree${refresh ? "?refresh=true" : ""}`),
  map: (id: string, refresh = false) => getJson<MapInfo>(`/api/v1/maps/${id}${refresh ? "?refresh=true" : ""}`),
  points: (id: string, refresh = false) => getJson<PointItem[]>(`/api/v1/maps/${id}/points${refresh ? "?refresh=true" : ""}`),
  labels: (id: string, refresh = false) => getJson<LabelGroup[]>(`/api/v1/maps/${id}/labels${refresh ? "?refresh=true" : ""}`),
  //: M7.4（§十一/§十四）：这张图能去哪 + 导航上下文（没有图库时 available=false，不是错误）。
  mapTransitions: (id: string) => getJson<MapTransitionsPayload>(`/api/v1/maps/${id}/transitions`),
  point: (id: number | string) => getJson<PointDetail>(`/api/v1/points/${id}`),
  search: (q: string) => getJson<SearchResult>(`/api/v1/search?q=${encodeURIComponent(q)}`),
  grease: () => getJson<GreaseTopic>("/api/v1/topics/floating-grease"),
  topic: (key: string) => getJson<TopicPayload>(`/api/v1/topics/${key}`),
  atlas: () => getJson<AtlasPayload>("/api/v1/guides/atlas"),
  settings: () => getJson<SettingsInfo>("/api/v1/settings"),
  dataSource: () => getJson<DataSource>("/api/v1/data-source"),
  setDataSource: (mode: DataSource["mode"]) => sendJson<DataSource>("/api/v1/data-source", "PUT", { mode }),
  mapRevision: (id: string) => getJson<{ map_id: string; hash: string | null; source: string }>(`/api/v1/maps/${id}/revision`),
  checkUpdate: () => getJson<{ remote_enabled: boolean; message: string; snapshot_command?: string }>("/api/v1/updates/check"),
  userPoint: (sourceId: string) => getJson<UserPoint>(`/api/v1/user/points/${sourceId}`),
  userPoints: () => getJson<{ points: UserPoint[] }>("/api/v1/user/points"),
  saveUserPoint: (sourceId: string, body: Partial<UserPoint>) => sendJson<UserPoint>(`/api/v1/user/points/${sourceId}`, "PUT", body),
  exportUser: () => getJson<{ version: number; points: UserPoint[] }>("/api/v1/user/export"),
  importUser: (payload: unknown) => sendJson<{ imported: number }>("/api/v1/user/import", "POST", payload),
  guides: (sourceId: string) => getJson<{ entries: GuideEntry[]; available?: boolean }>(`/api/v1/guides/by-point/${sourceId}`),
  //: a1-8 十三：点位详情与 Atlas 都要能回答「凭什么算完成」。
  pointEvidence: (sourceId: string) => getJson<PointEvidencePayload>(`/api/v1/guides/evidence/${sourceId}`),
  evidenceOverview: () => getJson<EvidenceOverview>("/api/v1/guides/evidence"),
  guideIndex: () => getJson<{ points: Record<string, number> }>("/api/v1/guides/index"),
  createGuide: (body: unknown) => sendJson<GuideEntry>("/api/v1/guides", "POST", body),
  //: a1-9 Phase 6（P6.6）个人进度层：三个只读接口；accept_map_mark 只改变展示口径，不写任何数据。
  progressStatus: () => getJson<ProgressStatus>("/api/v1/progress/status"),
  progressPoints: (acceptMapMark = false) =>
    getJson<ProgressPointsPayload>(`/api/v1/progress/points?accept_map_mark=${acceptMapMark ? "true" : "false"}`),
  progressAtlas: (acceptMapMark = false, region?: string) => {
    const params = new URLSearchParams({ accept_map_mark: acceptMapMark ? "true" : "false" });
    if (region) params.set("region", region);
    return getJson<ProgressAtlasPayload>(`/api/v1/progress/atlas?${params.toString()}`);
  },
  golden: () => getJson<unknown>("/api/v1/debug/golden-20"),
};
