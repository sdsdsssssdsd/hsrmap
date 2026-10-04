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

/**
 * P6.6 增量：是否被「隐藏已标记完成的点位」开关挡掉。
 * 只认 completed —— remaining / conflict / unclear 一律不动：
 * conflict（本地已完成但远端说没有）与 unclear（远端给了未证实的状态）都不等于完成。
 * 这个条件与 matchProgressFilter 正交，两者同时成立才可见。
 */
export function hidesCompletedPoint(state: ProgressPointState | undefined, hideCompleted: boolean): boolean {
  return hideCompleted && state === "completed";
}
