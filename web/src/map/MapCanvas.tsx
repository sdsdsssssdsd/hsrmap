import { useEffect, useRef } from "react";
import type { MapInfo, PointItem } from "../api/types";
import { MapController } from "./MapController";

interface Props {
  info: MapInfo | null;
  points: PointItem[];
  selectedLabels: string[];
  focusId?: string | null;
  guideIds?: string[];
  onSelect: (point: PointItem) => void;
  controllerRef: { current: MapController | null };
}

export function MapCanvas({ info, points, selectedLabels, focusId, guideIds = [], onSelect, controllerRef }: Props) {
  const elRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!elRef.current || !info) return;
    const controller = new MapController();
    controller.onSelect = onSelect;
    controller.mount(elRef.current, info);
    controller.loadPoints(points, new Set(selectedLabels), new Set(guideIds));
    controllerRef.current = controller;
    if (focusId) controller.focusPoint(focusId);
    return () => {
      controller.destroy();
      if (controllerRef.current === controller) controllerRef.current = null;
    };
  }, [info?.id]);

  useEffect(() => {
    controllerRef.current?.loadPoints(points, new Set(selectedLabels), new Set(guideIds));
    if (focusId) controllerRef.current?.focusPoint(focusId);
  }, [points, selectedLabels.join(","), guideIds.join(",")]);

  useEffect(() => {
    if (focusId) controllerRef.current?.focusPoint(focusId);
  }, [focusId, points]);

  return <div id="map-canvas" ref={elRef} />;
}
