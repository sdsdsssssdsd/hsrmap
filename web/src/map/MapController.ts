import L from "leaflet";
import type { MapInfo, PointItem } from "../api/types";
import { createHsrCrs } from "./crs";
import { addSingleImageRaster, applyRasterView } from "./raster";

export class MapController {
  map: L.Map | null = null;
  groups = new Map<string, L.LayerGroup>();
  markers = new Map<string, L.Marker>();
  pointLabels = new Map<string, string[]>();
  lastFocusId: string | null = null;
  onSelect: ((point: PointItem) => void) | null = null;

  mount(el: HTMLElement, info: MapInfo) {
    this.destroy();
    el.replaceChildren();
    delete (el as HTMLElement & { _leaflet_id?: number })._leaflet_id;
    const crs = createHsrCrs(info.origin[0], info.origin[1]);
    this.map = L.map(el, {
      crs,
      minZoom: -8,
      maxZoom: 4,
      zoomSnap: 0,
      zoomDelta: 0.5,
      zoomAnimation: false,
      fadeAnimation: false,
      markerZoomAnimation: false,
      attributionControl: false,
      zoomControl: false,
    });
    const overlay = addSingleImageRaster(this.map, info);
    applyRasterView(this.map, info);
    overlay.on("load", () => {
      overlay.setOpacity(1);
      applyRasterView(this.map!, info);
    });
  }

  loadPoints(points: PointItem[], selected: Set<string>, guideIds: Set<string> = new Set()) {
    if (!this.map) return;
    for (const marker of new Set(this.markers.values())) this.map.removeLayer(marker);
    for (const group of this.groups.values()) group.remove();
    this.groups.clear();
    this.markers.clear();
    this.pointLabels.clear();
    for (const point of points) {
      const labelIds = point.labels.map((label) => label.id);
      this.pointLabels.set(String(point.id), labelIds);
      const labelId = labelIds[0] || "none";
      let group = this.groups.get(labelId);
      if (!group) {
        group = L.layerGroup();
        this.groups.set(labelId, group);
      }
      const iconUrl = point.labels[0]?.icon;
      const icon = iconUrl
        ? L.divIcon({
            className: "hsr-marker",
            iconSize: [36, 36],
            iconAnchor: [18, 18],
            html: `<span class="hsr-marker-hit${guideIds.has(point.source_id) ? " has-guide" : ""}"><img src="${iconUrl}" alt="" width="24" height="24" /></span>${guideIds.has(point.source_id) ? '<span class="hsr-guide-badge" aria-label="has-guide"></span>' : ""}`,
          })
        : L.divIcon({
            className: "hsr-marker empty",
            iconSize: [22, 22],
            iconAnchor: [11, 11],
            html: `<span class="hsr-marker-hit hsr-marker-dot${guideIds.has(point.source_id) ? " has-guide" : ""}"></span>${guideIds.has(point.source_id) ? '<span class="hsr-guide-badge" aria-label="has-guide"></span>' : ""}`,
          });
      const marker = L.marker([point.y, point.x], { icon, riseOnHover: true, keyboard: false });
      if (guideIds.has(point.source_id)) {
        marker.bindTooltip("📖 有攻略", { direction: "top", offset: [0, -14], opacity: 0.95 });
      }
      marker.on("mousedown", () => this.focusPoint(String(point.id)));
      marker.on("click", (event) => {
        L.DomEvent.stop(event);
        this.focusPoint(String(point.id));
        this.onSelect?.(point);
      });
      marker.addTo(group);
      this.markers.set(String(point.id), marker);
      this.markers.set(point.source_id, marker);
    }
    this.setVisibleLabels(selected);
    if (this.lastFocusId) this.focusPoint(this.lastFocusId);
  }

  setVisibleLabels(selected: Set<string>) {
    if (!this.map) return;
    const seen = new Set<L.Marker>();
    for (const [id, marker] of this.markers) {
      if (seen.has(marker)) continue;
      seen.add(marker);
      const labels = this.pointLabels.get(id) || [];
      const show = labels.some((labelId) => selected.has(labelId));
      if (show) marker.addTo(this.map);
      else this.map.removeLayer(marker);
    }
  }

  focusPoint(id: string) {
    this.lastFocusId = String(id);
    const marker = this.markers.get(String(id));
    document.querySelectorAll(".hsr-marker-focus").forEach((node) => node.classList.remove("hsr-marker-focus"));
    for (const item of new Set(this.markers.values())) item.setZIndexOffset(0);
    if (!marker || !this.map) return;
    marker.setZIndexOffset(800);
    marker.getElement()?.classList.add("hsr-marker-focus");
  }

  zoomBy(delta: number) {
    if (!this.map) return;
    this.map.setZoom(this.map.getZoom() + delta);
  }

  setZoom(value: number) {
    this.map?.setZoom(value);
  }

  getZoom() {
    return this.map?.getZoom() ?? 0;
  }

  clearFocus() {
    this.lastFocusId = null;
    document.querySelectorAll(".hsr-marker-focus").forEach((node) => node.classList.remove("hsr-marker-focus"));
    for (const item of new Set(this.markers.values())) item.setZIndexOffset(0);
  }

  destroy() {
    if (this.map) {
      this.map.remove();
      this.map = null;
    }
    this.groups.clear();
    this.markers.clear();
    this.pointLabels.clear();
    this.lastFocusId = null;
  }
}
