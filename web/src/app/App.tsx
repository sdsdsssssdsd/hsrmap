import { ChevronLeft, ChevronRight, Eye, EyeOff, Search, Settings, Star, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { AtlasPayload, AtlasTopic, DataSource, EvidenceOverview, EvidenceStep, GuideEntry, GreaseTopic, PointEvidencePayload, PointItem, SearchResult, SettingsInfo, TopicPayload, TreeNode, UserPoint } from "../api/types";
import { MapCanvas } from "../map/MapCanvas";
import { MapController } from "../map/MapController";
import { MapNav } from "../navigation/MapNav";
import { ancestorIds, displayChildren, firstRenderable, indexTree, pathNames, rootIdOf } from "../navigation/tree";
import { parseHash, writeHash } from "./router";
import { useViewer } from "./store";

function formatBytes(n: number) {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(0)} MB`;
  return `${n} B`;
}

function sourceChip(ds: DataSource) {
  if (ds.source === "live") return { label: "● 在线", cls: "source-live" };
  if (ds.source === "live-cache") return { label: "● 缓存", cls: "source-cache" };
  if (ds.mode !== "offline" && ds.stale) return { label: "● 已回退", cls: "source-fallback" };
  return { label: "● 离线", cls: "source-snapshot" };
}

function sourceMapText(ds: DataSource) {
  if (ds.source === "live") return "官方实时数据";
  if (ds.source === "live-cache") return "在线缓存";
  if (ds.mode !== "offline" && ds.stale) return "官方连接失败，正在使用本地快照";
  return "正在使用本地快照";
}

/** a1-8 十三：证据等级必须一眼看得出来源，推断不能和正文引证长一个样子。 */
const LEVEL_LABEL: Record<string, string> = {
  OFFICIAL: "官方",
  COMMUNITY_TEXT: "正文引证",
  TRANSCRIPTION: "图解转录",
  CROSS_INFERENCE: "交叉推断",
};

const TIER_LABEL: Record<string, string> = {
  EXACT: "逐字一致",
  FRAGMENT: "分段一致",
  ASSEMBLED: "拼装标签",
  IMAGE_REF: "指向图片",
  DERIVED: "推导",
};

function levelClass(level: string | null | undefined): string {
  if (level === "CROSS_INFERENCE") return "ev ev-inference";
  if (level === "TRANSCRIPTION") return "ev ev-transcription";
  if (level === "OFFICIAL") return "ev ev-official";
  return "ev ev-community";
}

function statusText(row: { status: string }): string {
  const names: Record<string, string> = {
    COMPLETE: "完成（定位 + 解法）",
    LOCATE_COMPLETE: "完成（到点即得）",
    SOLVE_MISSING: "缺解法",
    LOCATE_MISSING: "缺定位",
    SCOPE_ONLY: "只有范围证据",
    NO_EVIDENCE: "无证据",
  };
  return names[row.status] || row.status;
}

function EvidenceMark({ step }: { step?: EvidenceStep }) {
  if (!step || !step.evidence_level) return <span className="ev ev-none">无声明</span>;
  const asset = step.transcribed && step.asset_sha256 ? " " + step.asset_sha256.slice(0, 8) : "";
  return (
    <span className={levelClass(step.evidence_level)}>
      {LEVEL_LABEL[step.evidence_level] || step.evidence_level}
      {step.inferred ? "（推断，不是原文）" : ""}
      {asset}
    </span>
  );
}
function levelText(level: string | null | undefined): string {
  if (!level) return "—";
  return LEVEL_LABEL[level] || level;
}

/** 展开的证据：来源、落地方式、依据图片、推断依据（a1-8 十三）。 */
function EvidenceDetails({ step }: { step?: EvidenceStep }) {
  if (!step) return null;
  const parts: string[] = [];
  if (step.grounding_tier) parts.push("落地 " + (TIER_LABEL[step.grounding_tier] || step.grounding_tier));
  if (step.official_point_id) parts.push("官方点位 " + step.official_point_id);
  if (step.source_page_id) parts.push("来源页 #" + step.source_page_id);
  if (step.method_version) parts.push(step.method_version);
  return (
    <div className="evidence-detail">
      {parts.length > 0 && <p className="meta">{parts.join(" · ")}</p>}
      {step.basis.length > 0 && <p className="meta">推断依据：{step.basis.join(" · ")}</p>}
      {step.asset_sha256 && (
        <p className="meta">
          <a href={"/guide-assets/" + step.asset_sha256} target="_blank" rel="noreferrer">查看证据图 ↗</a>
        </p>
      )}
    </div>
  );
}
const MODE_COPY = {
  offline: { title: "完全离线", hint: "始终使用本地快照" },
  hybrid: { title: "在线优先", hint: "使用官方当前数据；网络异常时自动使用本地快照" },
  live: { title: "强制在线", hint: "仅使用官方当前数据；失败时显示错误" },
} as const;

export function App() {
  const state = useViewer();
  const controllerRef = useRef<MapController | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult | null>(null);
  const [zoom, setZoom] = useState(0);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [greaseOpen, setGreaseOpen] = useState(false);
  const [grease, setGrease] = useState<GreaseTopic | null>(null);
  const [greaseFilter, setGreaseFilter] = useState<"all" | "todo" | "done">("all");
  const [atlasOpen, setAtlasOpen] = useState(false);
  const [atlas, setAtlas] = useState<AtlasPayload | null>(null);
  const [activeTopic, setActiveTopic] = useState<AtlasTopic | null>(null);
  const [topicMaps, setTopicMaps] = useState<TopicPayload | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settings, setSettings] = useState<SettingsInfo | null>(null);
  const [updateMsg, setUpdateMsg] = useState("");
  const [userPoint, setUserPoint] = useState<UserPoint | null>(null);
  const [userIndex, setUserIndex] = useState<Record<string, UserPoint>>({});
  const [guides, setGuides] = useState<GuideEntry[]>([]);
  const [guideIndex, setGuideIndex] = useState<Record<string, number>>({});
  const [guideDraft, setGuideDraft] = useState("");
  const [pointEvidence, setPointEvidence] = useState<PointEvidencePayload | null>(null);
  const [evidenceOverview, setEvidenceOverview] = useState<EvidenceOverview | null>(null);
  const [dataSource, setDataSource] = useState<DataSource | null>(null);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [mapHash, setMapHash] = useState<string | null>(null);
  const [liveBanner, setLiveBanner] = useState("");
  const index = useMemo(() => indexTree(state.tree), [state.tree]);
  //: 步骤 -> 证据声明：详情页每一步都要能说出自己凭什么（a1-8 十三）。
  const claimFor = (guideId: number, stepIndex: number): EvidenceStep | undefined =>
    pointEvidence?.entries.find((item) => item.guide_id === guideId)?.steps.find((step) => step.index === stepIndex);
  const problemsFor = (guideId: number): { problem: string; detail?: string }[] =>
    pointEvidence?.entries.find((item) => item.guide_id === guideId)?.problems || [];

  useEffect(() => {
    Promise.all([api.tree(), api.userPoints()]).then(([tree, users]) => {
      const { parentById } = indexTree(tree);
      const route = parseHash();
      const fallback = firstRenderable(tree.find((n) => n.name.includes("空间站")) || tree[0]);
      const mapId = route.mapId && indexTree(tree).nodesById.get(route.mapId)?.renderable ? route.mapId : fallback;
      const worldId = mapId ? rootIdOf(mapId, parentById) : tree[0]?.id;
      useViewer.getState().set({ tree, worldId, mapId: mapId || null, selectedPointId: route.pointId });
      if (mapId) writeHash({ mapId, pointId: route.pointId, labels: route.labels });
      const byId: Record<string, UserPoint> = {};
      for (const item of users.points) byId[item.source_point_id] = item;
      setUserIndex(byId);
      if (mapId) setExpanded(new Set(ancestorIds(mapId, parentById)));
    });
    void api.dataSource().then(setDataSource);
    void api.guideIndex().then((body) => setGuideIndex(body.points));
  }, []);

  useEffect(() => {
    const onHash = () => {
      const route = parseHash();
      if (route.mapId) {
        useViewer.getState().set({ mapId: route.mapId, selectedPointId: route.pointId });
      }
    };
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
    };
  }, []);

  useEffect(() => {
    const mapId = state.mapId;
    if (!mapId) return;
    let cancelled = false;
    Promise.all([api.map(mapId), api.points(mapId), api.labels(mapId)]).then(([mapInfo, points, labelGroups]) => {
      if (cancelled) return;
      const route = parseHash();
      const allIds = labelGroups.flatMap((g) => g.labels.map((l) => l.id));
      const selectedLabels = route.labels && route.labels.length ? route.labels : allIds;
      const live = indexTree(useViewer.getState().tree);
      const worldId = rootIdOf(mapId, live.parentById);
      useViewer.getState().set({ mapInfo, points, labelGroups, selectedLabels, worldId, detail: null });
      setExpanded((prev) => new Set([...prev, ...ancestorIds(mapId, live.parentById)]));
      const wanted = route.pointId || state.selectedPointId;
      if (wanted) {
        const point = points.find((p) => String(p.id) === wanted || p.source_id === wanted);
        if (point) void openDetail(point);
      }
      void api.mapRevision(mapId).then((rev) => setMapHash(rev.hash));
      void api.dataSource().then(setDataSource);
    }).catch(() => {
      if (cancelled) return;
      setLiveBanner("官方连接失败");
      void api.dataSource().then(setDataSource);
    });
    return () => {
      cancelled = true;
    };
  }, [state.mapId]);

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults(null);
      return;
    }
    const timer = setTimeout(() => {
      void api.search(query.trim()).then(setResults);
    }, 280);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!state.mapId) return;
    const timer = setInterval(() => {
      void api.mapRevision(state.mapId!).then((rev) => {
        if (rev.hash && mapHash && rev.hash !== mapHash) setLiveBanner("官方地图已更新");
      });
    }, 5 * 60 * 1000);
    return () => clearInterval(timer);
  }, [state.mapId, mapHash]);

  function openMap(mapId: string, pointId?: string | null, labels?: string[] | null) {
    writeHash({ mapId, pointId, labels });
    state.set({ mapId, selectedPointId: pointId || null, worldOpen: false, searchOpen: false });
    setGreaseOpen(false);
    setAtlasOpen(false);
    setActiveTopic(null);
  }

  function activateNode(node: TreeNode) {
    if (node.renderable) {
      openMap(node.id);
      return;
    }
    const child = firstRenderable(node);
    if (child) openMap(child);
  }

  async function reloadActiveMap(refresh = false) {
    const id = useViewer.getState().mapId;
    if (!id) {
      setDataSource(await api.dataSource());
      return;
    }
    try {
      const [mapInfo, points, labelGroups] = await Promise.all([api.map(id, refresh), api.points(id, refresh), api.labels(id, refresh)]);
      const allIds = labelGroups.flatMap((g) => g.labels.map((l) => l.id));
      useViewer.getState().set({ mapInfo, points, labelGroups, selectedLabels: allIds, detail: null });
      setMapHash((await api.mapRevision(id)).hash);
      setDataSource(await api.dataSource());
      setLiveBanner("");
    } catch {
      setLiveBanner("官方连接失败");
      setDataSource(await api.dataSource());
    }
  }

  async function openDetail(point: PointItem) {
    const selectedId = String(point.id);
    const current = useViewer.getState();
    current.set({
      selectedPointId: selectedId,
      searchOpen: false,
      worldOpen: false,
      detail: {
        core: {
          point_id: selectedId,
          source_id: point.source_id,
          map_id: current.mapId || "",
          x: point.x,
          y: point.y,
        },
        labels: point.labels.map((label) => ({ id: label.id, name: label.name })),
        detail: { state: "LOADING", text: null, images: [] },
      },
    });
    setSourceOpen(false);
    setSettingsOpen(false);
    if (current.mapId) writeHash({ mapId: current.mapId, pointId: selectedId, labels: current.selectedLabels.length ? current.selectedLabels : null });
    controllerRef.current?.focusPoint(selectedId);
    try {
      const [detail, progress, guideBody, evidenceBody] = await Promise.all([
        api.point(point.id),
        api.userPoint(point.source_id),
        api.guides(point.source_id),
        api.pointEvidence(point.source_id).catch(() => null),
      ]);
      if (useViewer.getState().selectedPointId !== selectedId) return;
      useViewer.getState().set({ detail });
      setUserPoint(progress);
      setGuides(guideBody.entries);
      setPointEvidence(evidenceBody);
      setGuideDraft("");
    } catch {
      if (useViewer.getState().selectedPointId !== selectedId) return;
      useViewer.getState().set({
        detail: {
          core: { point_id: selectedId, source_id: point.source_id, map_id: current.mapId || "", x: point.x, y: point.y },
          labels: point.labels.map((label) => ({ id: label.id, name: label.name })),
          detail: { state: "UNAVAILABLE", text: null, images: [] },
        },
      });
    }
  }

  function toggleLabel(id: string) {
    const next = state.selectedLabels.includes(id)
      ? state.selectedLabels.filter((item) => item !== id)
      : [...state.selectedLabels, id];
    state.set({ selectedLabels: next });
    if (state.mapId) writeHash({ mapId: state.mapId, pointId: state.selectedPointId, labels: next });
  }

  async function saveProgress(patch: Partial<UserPoint>) {
    if (!state.detail) return;
    const sourceId = state.detail.core.source_id;
    const saved = await api.saveUserPoint(sourceId, { ...userPoint, ...patch, source_point_id: sourceId });
    setUserPoint(saved);
    setUserIndex((prev) => ({ ...prev, [sourceId]: saved }));
  }

  const derivedWorldId = state.mapId ? rootIdOf(state.mapId, index.parentById) : state.worldId;
  const world = state.tree.find((node) => node.id === derivedWorldId) || state.tree[0];
  const worldChildren = world?.children || [];
  const allLabelIds = state.labelGroups.flatMap((g) => g.labels.map((l) => l.id));
  const noneSelected = state.selectedLabels.length === 0;
  const navNodes = useMemo(() => displayChildren(worldChildren), [worldChildren]);

  return (
    <div className={`app${state.sidebarOpen ? "" : " sidebar-closed"}`}>
      <aside className={`sidebar${state.sidebarOpen ? "" : " closed"}`}>
        <div className="world-header" onClick={() => state.set({ worldOpen: !state.worldOpen })}>
          <span>{world?.name || "选择世界"}</span>
          <ChevronRight size={16} />
        </div>
        <button className="overview-btn" onClick={() => world && activateNode(world)}>
          全息总览
        </button>
        <button
          className="grease-btn"
          onClick={() => {
            setAtlasOpen(true);
            setActiveTopic(null);
            setTopicMaps(null);
            if (!atlas) void api.atlas().then(setAtlas);
            if (!evidenceOverview) void api.evidenceOverview().then(setEvidenceOverview).catch(() => null);
          }}
        >
          攻略中心
        </button>
        {atlasOpen && !atlas ? (
          <div className="grease-panel">
            <p>加载中…</p>
          </div>
        ) : atlasOpen && !activeTopic && atlas ? (
          <div className="grease-panel">
            <div className="grease-head">
              <strong>攻略中心</strong>
              <span>{atlas.topics.length} 主题</span>
            </div>
            {evidenceOverview && (
              <div className="layers">
                <div className="layers-head">
                  <strong>完成与证据</strong>
                  <span className="meta">为什么算完成</span>
                </div>
                <div className="layer-row">
                  <span>已完成</span>
                  <strong>{evidenceOverview.completion.done} / {evidenceOverview.completion.points}</strong>
                </div>
                <div className="layer-row">
                  <span>正文引证即可</span>
                  <strong>{evidenceOverview.layers.direct}</strong>
                </div>
                <div className="layer-row">
                  <span>需要图解转录</span>
                  <strong>{evidenceOverview.layers.transcription}</strong>
                </div>
                <div className="layer-row">
                  <span>需要交叉推断</span>
                  <strong>{evidenceOverview.layers.inference}</strong>
                </div>
                <div className="layer-row">
                  <span>未完成</span>
                  <strong>{evidenceOverview.layers.missing}</strong>
                </div>
                <p className="meta">
                  声明 {evidenceOverview.claims.total} 条 · 证据摘要 {evidenceOverview.claims.digest.slice(0, 12)}…
                </p>
              </div>
            )}
            {(["PUZZLE", "CHALLENGE", "COLLECTIBLE"] as const).map((kind) => {
              const rows = atlas.topics.filter((item) => item.guide_kind === kind);
              if (!rows.length) return null;
              const title = kind === "PUZZLE" ? "机关解谜" : kind === "CHALLENGE" ? "操作挑战" : "特殊收集";
              return (
                <div key={kind}>
                  <div className="grease-head">
                    <span>{title}</span>
                  </div>
                  {rows.map((item) => {
                    const published = item.real_guide_coverage.with_approved_guide;
                    const total = item.scope === "MAP_LABEL" ? item.official_maps || item.official_targets : item.official_targets || item.official_points;
                    const disabled = item.enabled === false;
                    const labelOnly = item.official_status === "LABEL_EXISTS_NO_POINTS" || !total;
                    return (
                      <button
                        key={item.topic}
                        className="grease-map"
                        onClick={() => {
                          setActiveTopic(item);
                          if (item.topic === "floating_grease") {
                            setGreaseOpen(true);
                            if (!grease) void api.grease().then(setGrease);
                          } else {
                            void api.topic(item.topic).then(setTopicMaps);
                          }
                        }}
                      >
                        <span>{item.display_name}{disabled ? "（未启用）" : ""}</span>
                        <span>
                          {disabled
                            ? "Wave 4"
                            : labelOnly
                              ? `${published} GLOBAL`
                              : `${published} / ${total}`}
                          {!disabled && !labelOnly && total > 0 && published === total ? " ✓" : ""}
                        </span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
            <button className="clear-all" onClick={() => setAtlasOpen(false)}>
              返回地图树
            </button>
          </div>
        ) : greaseOpen && activeTopic?.topic === "floating_grease" && !grease ? (
          <div className="grease-panel">
            <p>加载中…</p>
          </div>
        ) : greaseOpen && grease ? (
          <div className="grease-panel">
            <div className="grease-head">
              <strong>{grease.origin.name}</strong>
              <span>
                {grease.origin.count} 解谜 · {grease.origin.maps.length} 地图
              </span>
            </div>
            <div className="grease-head">
              <span>{grease.notes.name}</span>
              <span>{grease.notes.count}</span>
            </div>
            <div className="grease-filters">
              <button className={greaseFilter === "all" ? "on" : ""} onClick={() => setGreaseFilter("all")}>
                全部
              </button>
              <button className={greaseFilter === "todo" ? "on" : ""} onClick={() => setGreaseFilter("todo")}>
                未完成
              </button>
              <button className={greaseFilter === "done" ? "on" : ""} onClick={() => setGreaseFilter("done")}>
                已完成
              </button>
            </div>
            {grease.origin.maps.map((item) => {
              const done = item.points.filter((p) => userIndex[p.source_id]?.completed).length;
              if (greaseFilter === "todo" && done === item.count) return null;
              if (greaseFilter === "done" && done === 0) return null;
              return (
                <button
                  key={item.map_id}
                  className="grease-map"
                  onClick={() => openMap(item.map_id, String(item.points[0]?.id), [grease.origin.label_id])}
                >
                  <span>{item.path || item.name}</span>
                  <span>
                    {done} / {item.count}
                    {done === item.count ? " ✓" : ""}
                  </span>
                </button>
              );
            })}
            <button
              className="clear-all"
              onClick={() => {
                setGreaseOpen(false);
                setActiveTopic(null);
              }}
            >
              返回攻略中心
            </button>
          </div>
        ) : activeTopic && !topicMaps && activeTopic.topic !== "floating_grease" ? (
          <div className="grease-panel">
            <p>加载中…</p>
          </div>
        ) : activeTopic && topicMaps ? (
          <div className="grease-panel">
            <div className="grease-head">
              <strong>{activeTopic.display_name}</strong>
              <span>
                {(topicMaps.origin || topicMaps).count} 点 · {(topicMaps.origin || topicMaps).maps.length} 地图
              </span>
            </div>
            {(topicMaps.origin || topicMaps).maps.map((item) => {
              const done = item.points.filter((p) => userIndex[p.source_id]?.completed).length;
              return (
                <button
                  key={item.map_id}
                  className="grease-map"
                  onClick={() => openMap(item.map_id, item.points[0] ? String(item.points[0].id) : null)}
                >
                  <span>{item.path || item.name}</span>
                  <span>
                    {done} / {item.count}
                    {done === item.count && item.count > 0 ? " ✓" : ""}
                  </span>
                </button>
              );
            })}
            <button
              className="clear-all"
              onClick={() => {
                setActiveTopic(null);
                setTopicMaps(null);
              }}
            >
              返回攻略中心
            </button>
          </div>
        ) : (
          <div className="map-nav">
            <MapNav
              nodes={navNodes}
              mapId={state.mapId}
              expanded={expanded}
              onToggle={(id) => {
                setExpanded((prev) => {
                  const next = new Set(prev);
                  if (next.has(id)) next.delete(id);
                  else next.add(id);
                  return next;
                });
              }}
              onActivate={activateNode}
            />
          </div>
        )}
        <div className="label-panel">
          {state.labelGroups.map((group) => {
            const collapsed = state.collapsedCats.includes(group.category.id);
            const ids = group.labels.map((label) => label.id);
            const hidden = ids.every((id) => !state.selectedLabels.includes(id));
            return (
              <div key={group.category.id}>
                <div className="label-cat">
                  <span onClick={() => {
                    const next = collapsed
                      ? state.collapsedCats.filter((id) => id !== group.category.id)
                      : [...state.collapsedCats, group.category.id];
                    state.set({ collapsedCats: next });
                  }}>
                    {group.category.name}
                  </span>
                  <span className="cat-actions">
                    <button
                      className="icon-inline"
                      onClick={() => {
                        const next = hidden
                          ? [...new Set([...state.selectedLabels, ...ids])]
                          : state.selectedLabels.filter((id) => !ids.includes(id));
                        state.set({ selectedLabels: next });
                      }}
                      aria-label="toggle-category"
                    >
                      {hidden ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                    <span onClick={() => {
                      const next = collapsed
                        ? state.collapsedCats.filter((id) => id !== group.category.id)
                        : [...state.collapsedCats, group.category.id];
                      state.set({ collapsedCats: next });
                    }}>
                      {collapsed ? "▾" : "▴"}
                    </span>
                  </span>
                </div>
                {!collapsed &&
                  group.labels.map((label) => (
                    <label key={label.id} className="label-row">
                      <input
                        type="checkbox"
                        checked={state.selectedLabels.includes(label.id)}
                        onChange={() => toggleLabel(label.id)}
                      />
                      {label.icon ? <img src={label.icon} alt="" /> : <span />}
                      <span>{label.name}</span>
                      <span className="count">{label.count}</span>
                    </label>
                  ))}
              </div>
            );
          })}
        </div>
        <button
          className="clear-all"
          onClick={() => {
            const next = noneSelected ? allLabelIds : [];
            state.set({ selectedLabels: next });
            if (state.mapId) writeHash({ mapId: state.mapId, pointId: state.selectedPointId, labels: next });
          }}
        >
          {noneSelected ? "全部显示" : "取消全选"}
        </button>
      </aside>
      <div className="map-shell">
        <button className="sidebar-handle" onClick={() => state.set({ sidebarOpen: !state.sidebarOpen })}>
          {state.sidebarOpen ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
        </button>
        {state.worldOpen && (
          <div className="world-pop">
            {state.tree.map((node) => (
              <button
                key={node.id}
                onClick={() => {
                  state.set({ worldId: node.id, worldOpen: false });
                  const child = firstRenderable(node);
                  if (child) openMap(child);
                }}
              >
                {node.name}
              </button>
            ))}
          </div>
        )}
        <MapCanvas
          info={state.mapInfo}
          points={state.points}
          selectedLabels={state.selectedLabels}
          focusId={state.selectedPointId}
          guideIds={Object.keys(guideIndex)}
          onSelect={(point) => void openDetail(point)}
          controllerRef={controllerRef}
        />
        <div className="top-right">
          {dataSource && (
            <button
              className={`source-chip ${sourceChip(dataSource).cls}`}
              onClick={() => setSourceOpen((open) => !open)}
              aria-label="data-source"
            >
              {sourceChip(dataSource).label}
            </button>
          )}
          <a className="icon-btn" href="/review" aria-label="review" title="攻略审核">审</a>
          <button className="icon-btn" onClick={() => state.set({ searchOpen: !state.searchOpen })} aria-label="search">
            <Search size={16} />
          </button>
          <button
            className="icon-btn"
            aria-label="settings"
            onClick={() => {
              setSettingsOpen((open) => !open);
              if (!settings) void api.settings().then(setSettings);
            }}
          >
            <Settings size={16} />
          </button>
        </div>
        {state.searchOpen && (
          <div className="search-pop">
            <input
              value={query}
              placeholder="搜索地图 / 标签 / 说明"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={async (event) => {
                if (event.key === "Enter" && query.trim()) setResults(await api.search(query.trim()));
              }}
            />
            {results?.labels.map((item) => (
              <button key={`l${item.id}`} onClick={() => void jumpLabel(item.name, item.id)}>
                标签 · {item.name} · {item.count}
              </button>
            ))}
            {results?.maps.filter((item) => item.renderable).map((item) => (
              <button key={`m${item.id}`} onClick={() => openMap(item.id)}>
                地图 · {item.path}
              </button>
            ))}
            {results?.points.map((item) => (
              <button key={`p${item.id}`} onClick={() => openMap(item.map_id, String(item.id))}>
                点位 · {item.path} · {item.name}
              </button>
            ))}
          </div>
        )}
        {liveBanner && (
          <div className="live-banner">
            {liveBanner}
            <button
              onClick={() => {
                void reloadActiveMap(true);
              }}
            >
              应用更新
            </button>
          </div>
        )}
        {sourceOpen && dataSource && (
          <aside className="source-pop">
            <h3>数据源</h3>
            <p>模式：{MODE_COPY[dataSource.mode].title}</p>
            <p>当前地图：{sourceMapText(dataSource)}</p>
            <p>最后获取：{dataSource.fetched_at || "—"}</p>
            <p>本地备份：{dataSource.snapshot_id}</p>
            {dataSource.message && <p className="meta">{dataSource.message}</p>}
            <button onClick={() => void reloadActiveMap(true)}>刷新当前地图</button>
          </aside>
        )}
        {settingsOpen && settings && (
          <aside className="settings-pop">
            <h2>设置</h2>
            <h3>在线数据</h3>
            <p>连接状态　{dataSource ? sourceChip(dataSource).label : "—"}</p>
            <p>官方 Bundle　{dataSource?.bundle_sha256 ? dataSource.bundle_sha256.slice(0, 12) : "—"}</p>
            <p>API 状态　{dataSource?.message || (dataSource?.source === "live" ? "正常" : dataSource?.remote_enabled ? "未探测" : "离线")}</p>
            <button
              onClick={async () => {
                try {
                  useViewer.getState().set({ tree: await api.tree(true) });
                } catch {
                  setLiveBanner("官方连接失败");
                }
                setDataSource(await api.dataSource());
              }}
            >
              刷新状态
            </button>
            {dataSource?.bundle_changed && <p>有新官方数据</p>}
            <h3>数据模式</h3>
            {(["offline", "hybrid", "live"] as const).map((mode) => (
              <label key={mode} className="mode-row">
                <input
                  type="radio"
                  name="data-mode"
                  checked={dataSource?.mode === mode}
                  onChange={async () => {
                    try {
                      const next = await api.setDataSource(mode);
                      setDataSource(next);
                      const tree = await api.tree();
                      useViewer.getState().set({ tree });
                      await reloadActiveMap(true);
                    } catch {
                      setLiveBanner("官方连接失败");
                      setDataSource(await api.dataSource());
                    }
                  }}
                />
                <span>
                  <strong>{MODE_COPY[mode].title}</strong>
                  <span className="meta">{MODE_COPY[mode].hint}</span>
                </span>
              </label>
            ))}
            <h3>离线快照</h3>
            <p>当前版本　{settings.snapshot_id}</p>
            <p>Maps　{settings.maps}</p>
            <p>Points　{settings.points}</p>
            <p>Detail　{settings.detail_state}</p>
            <button
              onClick={async () => {
                const check = await api.checkUpdate();
                setUpdateMsg(`有新官方数据时，在终端运行 ${check.snapshot_command || dataSource?.snapshot_command || "python -m hsrmap sync"}`);
              }}
            >
              构建最新离线快照
            </button>
            <p className="meta">在线阅读与离线快照更新分开，不会写入 core.db / detail.db。</p>
            <h3>存储</h3>
            <p>地图资源　{formatBytes(settings.storage.core_assets_bytes)}</p>
            <p>点位图片　{formatBytes(settings.storage.detail_assets_bytes)}</p>
            <p>衍生缩略图　{formatBytes(settings.storage.thumbnail_bytes)}</p>
            <p>Tile Cache　{formatBytes(settings.storage.tile_cache_bytes)}</p>
            {updateMsg && <p>{updateMsg}</p>}
            <button
              onClick={async () => {
                const payload = await api.exportUser();
                try {
                  await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
                  setUpdateMsg("用户数据已复制到剪贴板");
                } catch {
                  setUpdateMsg("无法写入剪贴板");
                }
              }}
            >
              导出用户数据
            </button>
          </aside>
        )}
        {state.detail && (
          <aside className="drawer">
            <button className="drawer-close" onClick={() => { state.set({ detail: null, selectedPointId: null }); setUserPoint(null); controllerRef.current?.clearFocus(); }}>
              <X size={16} />
            </button>
            <h2>{state.detail.labels.map((l) => l.name).join(" / ") || "点位"}</h2>
            <p>{pathNames(state.detail.core.map_id, index.nodesById, index.parentById).join(" / ")}</p>
            <div className="drawer-actions">
              <label>
                <input type="checkbox" checked={!!userPoint?.completed} onChange={(e) => void saveProgress({ completed: e.target.checked })} />
                已完成
              </label>
              <button className="icon-inline" onClick={() => void saveProgress({ favorite: !userPoint?.favorite })}>
                <Star size={16} fill={userPoint?.favorite ? "currentColor" : "none"} /> 收藏
              </button>
            </div>
            <textarea
              placeholder="备注"
              value={userPoint?.note || ""}
              onChange={(event) => setUserPoint((prev) => ({ ...(prev || { source_point_id: state.detail!.core.source_id, completed: false, favorite: false, note: "", stable_key: null }), note: event.target.value }))}
              onBlur={() => void saveProgress({ note: userPoint?.note || "" })}
            />
            <h3>官方说明</h3>
            {state.detail.detail.state === "LOADING" && <p>正在加载说明…</p>}
            {state.detail.detail.state === "EMPTY" && <p>该点位暂无官方详细说明</p>}
            {state.detail.detail.state === "UNAVAILABLE" && <p>详细资料尚未同步</p>}
            {state.detail.detail.state === "NONEMPTY" && <p>{state.detail.detail.text}</p>}
            {state.detail.detail.images.length > 0 && <h3>官方图片</h3>}
            {state.detail.detail.images.map((image) => (
              <img key={image.url} src={image.url} alt="" loading="lazy" />
            ))}
            <p className="meta">
              Point ID {state.detail.core.source_id}　坐标 {state.detail.core.x}, {state.detail.core.y}
            </p>
            <h3>完成判定</h3>
            {pointEvidence?.status ? (
              <div className="evidence-box">
                <p>
                  <strong>{statusText(pointEvidence.status)}</strong>
                  <span className="meta"> 要求 {pointEvidence.status.requirement} · {pointEvidence.status.solve_kind}</span>
                </p>
                <p className="meta">
                  定位证据 {levelText(pointEvidence.status.locate_evidence)}　
                  解法证据 {levelText(pointEvidence.status.solve_evidence)}
                </p>
                <p className="meta">
                  主攻略 {pointEvidence.status.title || "—"}（{pointEvidence.status.source_kind || "—"}）
                </p>
                {pointEvidence.audit_available === false && <p className="meta">地图进程只读快照，未附带审计状态</p>}
              </div>
            ) : (
              <p className="meta">这个点位不在官方点位表里，暂无完成判定。</p>
            )}
            <h3>图文攻略</h3>
            {guides.length === 0 && <p>暂无本地图文攻略</p>}
            {guides.map((entry) => (
              <div key={entry.id} className="guide-entry">
                <strong>{entry.title}</strong>
                <span className="meta">{entry.source_kind} {entry.source_name || ""} {entry.author || ""}</span>
                {entry.source_url && (
                  <p>
                    <a href={entry.source_url} target="_blank" rel="noreferrer">查看原文 ↗</a>
                  </p>
                )}
                {problemsFor(entry.id).length > 0 && (
                  <p className="meta ev ev-problem">
                    审计：{problemsFor(entry.id).map((item) => item.problem).join(" / ")}
                  </p>
                )}
                {entry.steps.map((step) => (
                  <div key={step.index}>
                    <p>
                      Step {step.index + 1}　{step.text}
                    </p>
                    <p>
                      <EvidenceMark step={claimFor(entry.id, step.index)} />
                    </p>
                    <EvidenceDetails step={claimFor(entry.id, step.index)} />
                    {step.images.map((sha) => (
                      <img key={sha} src={"/guide-assets/" + sha} alt="" loading="lazy" />
                    ))}
                  </div>
                ))}
              </div>
            ))}
            <textarea placeholder="本地攻略（每行一步）" value={guideDraft} onChange={(e) => setGuideDraft(e.target.value)} />
            <button
              className="clear-all"
              onClick={async () => {
                if (!guideDraft.trim() || !state.detail) return;
                const steps = guideDraft.split("\n").map((text) => ({ text, images: [] }));
                await api.createGuide({
                  source_point_id: state.detail.core.source_id,
                  title: "本地攻略",
                  source_kind: "Local",
                  source_name: "本地",
                  steps,
                });
                setGuides((await api.guides(state.detail.core.source_id)).entries);
                setGuideIndex((await api.guideIndex()).points);
                setGuideDraft("");
              }}
            >
              保存本地攻略
            </button>
          </aside>
        )}
        <div className="zoom-bar">
          <button onClick={() => { controllerRef.current?.zoomBy(-0.5); setZoom(controllerRef.current?.getZoom() || 0); }}>−</button>
          <input
            type="range"
            min={-6}
            max={3}
            step={0.1}
            value={zoom}
            onChange={(event) => {
              const value = Number(event.target.value);
              controllerRef.current?.setZoom(value);
              setZoom(value);
            }}
          />
          <button onClick={() => { controllerRef.current?.zoomBy(0.5); setZoom(controllerRef.current?.getZoom() || 0); }}>+</button>
        </div>
      </div>
    </div>
  );

  async function jumpLabel(name: string, labelId: string) {
    const found = await api.search(name);
    const point = found.points[0];
    if (point) openMap(point.map_id, String(point.id), [labelId]);
  }
}
