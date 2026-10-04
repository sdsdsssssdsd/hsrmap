import { create } from "zustand";
import type { LabelGroup, MapInfo, PointDetail, PointItem, TreeNode } from "../api/types";

interface ViewerState {
  tree: TreeNode[];
  worldId: string | null;
  mapId: string | null;
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
