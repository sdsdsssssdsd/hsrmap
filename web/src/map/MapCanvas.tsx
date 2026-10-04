import { useEffect, useRef } from "react";
import type { MapInfo, PointItem, ProgressFilter, ProgressPointState } from "../api/types";
import { MapController } from "./MapController";

interface Props {
  info: MapInfo | null;
  points: PointItem[];
  selectedLabels: string[];
  focusId?: string | null;
  guideIds?: string[];
  /** P6.6：进度层 states 按 source_point_id 索引，没有条目的点位不算已完成。 */
  progressStates?: Record<string, ProgressPointState>;
  progressFilter?: ProgressFilter;
  /** P6.6 增量：隐藏已标记完成的点位，默认关闭。 */
  hideCompleted?: boolean;
  onSelect: (point: PointItem) => void;
  controllerRef: { current: MapController | null };
}

/** 复用同一个空对象，避免每次渲染都换掉 progressStates 的身份。 */
const NO_PROGRESS: Record<string, ProgressPointState> = {};

export function MapCanvas({
  info,
  points,
  selectedLabels,
  focusId,
  guideIds = [],
  progressStates = NO_PROGRESS,
  progressFilter = "all",
  hideCompleted = false,
  onSelect,
  controllerRef,
}: Props) {
  const elRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!elRef.current || !info) return;
    const controller = new MapController();
    controller.onSelect = onSelect;
    controller.mount(elRef.current, info);
    controller.loadPoints(points, new Set(selectedLabels), new Set(guideIds), progressStates);
    controllerRef.current = controller;
    if (focusId) controller.focusPoint(focusId);
    return () => {
      controller.destroy();
      if (controllerRef.current === controller) controllerRef.current = null;
    };
  }, [info?.id]);

  useEffect(() => {
    controllerRef.current?.loadPoints(points, new Set(selectedLabels), new Set(guideIds), progressStates);
    if (focusId) controllerRef.current?.focusPoint(focusId);
  }, [points, selectedLabels.join(","), guideIds.join(","), progressStates]);

  //: P6.6：切过滤档 / 隐藏已完成只改可见性，不重建标记。
  useEffect(() => {
    controllerRef.current?.setProgress(progressStates, progressFilter, hideCompleted);
  }, [progressStates, progressFilter, hideCompleted]);

  useEffect(() => {
    if (focusId) controllerRef.current?.focusPoint(focusId);
  }, [focusId, points]);

  return <div id="map-canvas" ref={elRef} />;
}
