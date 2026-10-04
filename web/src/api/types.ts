export interface TreeNode {
  id: string;
  name: string;
  type: "folder" | "map";
  renderable: boolean;
  children: TreeNode[];
}

export interface MapInfo {
  id: string;
  name: string;
  origin: number[];
  raster: { asset: string; live_url?: string | null };
  bounds: { south_west: number[]; north_east: number[] };
  max_bounds: { south_west: number[]; north_east: number[] };
  width: number;
  height: number;
}

export interface PointItem {
  id: number;
  source_id: string;
  x: number;
  y: number;
  raster_x: number;
  raster_y: number;
  labels: { id: string; name: string; icon: string | null }[];
}

export interface LabelGroup {
  category: { id: string; name: string };
  labels: { id: string; name: string; icon: string | null; count: number; semantic_key: string | null }[];
}

export interface SearchResult {
  labels: { id: string; name: string; count: number; semantic_key: string | null }[];
  maps: { id: string; name: string; path: string; renderable: boolean }[];
  points: { id: number; source_id: string; map_id: string; name: string; path: string }[];
}

export interface PointDetail {
  core: { point_id: string; source_id: string; map_id: string; x: number; y: number };
  labels: { id: string; name: string }[];
  detail: { state: string; text: string | null; images: { url: string; role: string }[] };
}

export interface GreaseTopic {
  origin: GreaseBucket;
  notes: GreaseBucket;
}

export interface GreaseBucket {
  semantic_key: string;
  label_id: string;
  name: string;
  count: number;
  maps: { map_id: string; name: string; path?: string; count: number; points: { id: number; source_id: string; x: number; y: number }[] }[];
}

export interface UserPoint {
  source_point_id: string;
  completed: boolean;
  favorite: boolean;
  note: string | null;
  stable_key: string | null;
}

export interface AtlasTopic {
  topic: string;
  display_name: string;
  scope: string;
  guide_kind: string;
  official_targets: number;
  official_maps: number;
  official_points: number;
  official_status?: string;
  enabled?: boolean;
  real_guide_coverage: { with_approved_guide: number; without_guide: number };
}

export interface AtlasPayload {
  topics: AtlasTopic[];
  publish: string;
  viewer_network?: number;
}

export interface TopicPayload {
  topic_key?: string;
  name: string;
  count: number;
  maps: { map_id: string; name: string; path?: string; count: number; points: { id: number; source_id: string; x: number; y: number }[] }[];
  origin?: TopicPayload;
}

export interface GuideEntry {
  id: number;
  source_point_id: string;
  title: string;
  summary: string | null;
  source_kind: string;
  source_name: string | null;
  source_url?: string | null;
  author?: string | null;
  steps: { index: number; text: string; images: string[] }[];
}

/** a1-8 十三：一条步骤的证据声明。CROSS_INFERENCE 必须显式按「推断」显示。 */
export interface EvidenceStep {
  index: number;
  step_id: number | null;
  text: string;
  claim_kind: string | null;
  evidence_level: string | null;
  grounding_tier: string | null;
  source_page_id: number | null;
  official_point_id: string | null;
  asset_sha256: string | null;
  basis: string[];
  method_version: string | null;
  inferred: boolean;
  transcribed: boolean;
}

export interface EvidenceEntry {
  guide_id: number;
  title: string;
  source_kind: string;
  source_name: string;
  source_url: string;
  author: string;
  steps: EvidenceStep[];
  problems: { problem: string; detail?: string }[];
}

/** 点位完成判定：为什么算完成（六状态 + 定位/解法各自靠什么证据）。 */
export interface PointStatus {
  topic: string;
  point: string;
  requirement: string;
  solve_kind: string;
  requirement_source: string;
  status: string;
  done: boolean;
  locate_evidence: string | null;
  solve_evidence: string | null;
  guide_id: number | null;
  source_kind: string;
  title: string;
  missing: string;
}

export interface PointEvidencePayload {
  point: string;
  available: boolean;
  audit_available?: boolean;
  status: PointStatus | null;
  entries: EvidenceEntry[];
}

export interface EvidenceOverview {
  available: boolean;
  completion: Record<string, number>;
  layers: { direct: number; transcription: number; inference: number; missing: number };
  claims: { total: number; by_level: Record<string, number>; digest: string };
  lookup: { mode?: string };
}

export interface SettingsInfo {
  snapshot_id: string;
  maps: number;
  points: number;
  detail_state: string;
  storage: {
    core_assets_bytes: number;
    detail_assets_bytes: number;
    thumbnail_bytes: number;
    tile_cache_bytes: number;
  };
}

export interface DataSource {
  mode: "offline" | "hybrid" | "live";
  source: string;
  fetched_at: string | null;
  snapshot_id: string;
  stale: boolean;
  bundle_sha256?: string | null;
  bundle_changed?: boolean;
  remote_enabled: boolean;
  message: string | null;
  snapshot_command?: string;
}

/** a1-9 Phase 6（P6.6）个人进度层：远端观察只展示，Gate 0 通过前不参与完成判定。 */
export type ProgressPointState = "completed" | "remaining" | "conflict" | "unclear";

/** 地图过滤档：conflict 档同时覆盖「冲突」与「说不清」，两者都不等于完成。 */
export type ProgressFilter = "all" | "remaining" | "completed" | "conflict";

export interface ProgressStoreInfo {
  profiles: number;
  observations: Record<string, number>;
  completed_by_semantic: Record<string, number>;
  manual_points: number;
  stores_cookie: boolean;
}

export interface ProgressProfile {
  profile_id: string;
  realm: string;
  region: string;
  uid_masked: string;
  created_at: string;
}

/** 本地进度与远端观察的差异四桶；dry_run 表示还没有真正合并。 */
export interface ProgressDiff {
  local_total: number;
  remote_total: number;
  both: number;
  local_only: number;
  remote_only: number;
  unknown: number;
  remote_by_semantic: Record<string, number>;
  allowed_remote_semantics: string[];
  dry_run: boolean;
}

export interface ProgressGate {
  allowed_remote_semantics: string[];
  note?: string;
  map_mark_accepted?: boolean;
  viewer_network?: number;
}

export interface ProgressTotals {
  collectible: number;
  effective_completed: number;
  remaining: number;
  conflict: number;
  unclear: number;
}

export interface ProgressStatus {
  available: boolean;
  viewer_network: number;
  realm: string;
  store?: ProgressStoreInfo;
  profiles?: ProgressProfile[];
  last_observed_at: string;
  diff?: ProgressDiff;
  gate?: ProgressGate;
  merge?: { available: boolean; how: string };
  tables: boolean;
  observations: number;
  message: string;
}

/** states 的键是官方 source_point_id，取值只有四种：completed / remaining / conflict / unclear。 */
export interface ProgressPointsPayload {
  available: boolean;
  viewer_network: number;
  gate?: ProgressGate;
  totals?: Partial<ProgressTotals>;
  states?: Record<string, ProgressPointState>;
  message?: string;
}

export interface ProgressAtlasPoint {
  source_point_id: string;
  topic: string;
  label: string;
  zone: string;
  region: string;
  map_name: string;
  map_path: string;
  map_id: string;
  x: number;
  y: number;
  state: ProgressPointState;
  completed: boolean;
  status: string;
  requirement: string;
  solve_kind: string;
  locate_evidence: string;
  solve_evidence: string;
  guide_id: number | null;
  title: string;
  missing: string;
}

export interface ProgressAtlasMap {
  region: string;
  map_name: string;
  map_path: string;
  map_id: string;
  collectible: number;
  remaining: number;
  points: ProgressAtlasPoint[];
}

export interface ProgressAtlasRegion {
  zone: string;
  collectible: number;
  remaining: number;
  completed: number;
  maps: ProgressAtlasMap[];
}

export interface ProgressAtlasTopic {
  topic: string;
  collectible: number;
  remaining: number;
  completed: number;
}

export interface ProgressAtlasPayload {
  available: boolean;
  viewer_network: number;
  totals?: Partial<ProgressTotals>;
  gate?: ProgressGate;
  regions: ProgressAtlasRegion[];
  topics: ProgressAtlasTopic[];
  message?: string;
}
