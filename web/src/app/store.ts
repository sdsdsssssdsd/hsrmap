import { create } from "zustand";
import type { LabelGroup, MapInfo, PointDetail, PointItem, TreeNode } from "../api/types";
import { EMPTY_ORIGINS, type OriginStack } from "./router";

interface ViewerState {
  tree: TreeNode[];
  worldId: string | null;
  mapId: string | null;
  /** M7.5（a1-8-1 §十二）：navigation stack —— 从哪张图的哪个点跳进来的（origin_map_id 逗号栈）。 */
  origins: OriginStack;
  selectedPointId: string | null;
  mapInfo: MapInfo | null;
  points: PointItem[];
  labelGroups: LabelGroup[];
  selectedLabels: string[];
  collapsedCats: string[];
  sidebarOpen: boolean;
  worldOpen: boolean;
  searchOpen: boolean;
  detail: PointDetail | null;
  set: (patch: Partial<ViewerState>) => void;
}

const sidebarPref = localStorage.getItem("hsrmap.sidebarOpen");

export const useViewer = create<ViewerState>((set) => ({
  tree: [],
  worldId: null,
  mapId: null,
  origins: EMPTY_ORIGINS,
  selectedPointId: null,
  mapInfo: null,
  points: [],
  labelGroups: [],
  selectedLabels: [],
  collapsedCats: [],
  sidebarOpen: sidebarPref !== "0",
  worldOpen: false,
  searchOpen: false,
  detail: null,
  set: (patch) => {
    if (patch.sidebarOpen !== undefined) localStorage.setItem("hsrmap.sidebarOpen", patch.sidebarOpen ? "1" : "0");
    set(patch);
  },
}));
