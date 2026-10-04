import type { ProgressFilter, ProgressPointState } from "../api/types";

/**
 * a1-9 Phase 6（P6.6）：单个点位是否通过当前进度过滤档。
 * - 只看进度层认识的四个状态；`undefined` 表示还没同步的普通点位。
 * - 未同步的点位不是已完成，所以「剩余」档必须照常显示它们。
 * - 冲突（本地已完成、远端说没有）与说不清（远端给了未证实的状态）都不等于完成，合并进「冲突」档。
 */
export function matchProgressFilter(state: ProgressPointState | undefined, filter: ProgressFilter): boolean {
  if (filter === "all") return true;
  if (filter === "remaining") return state !== "completed";
  if (filter === "completed") return state === "completed";
  return state === "conflict" || state === "unclear";
}
