import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./style.css";
import { createHsrCrs } from "./crs";
import { installNetworkGuard } from "./guard";
import { addSingleImageRaster, applyKernelView, type MapInfo } from "./raster";

installNetworkGuard();

interface PointItem {
  id: number;
  source_id: string;
  x: number;
  y: number;
  raster_x: number;
  raster_y: number;
  labels: { id: string; name: string; icon: string | null }[];
}

interface GoldenItem {
  source_point_id: number;
  source_coordinate: { x: number; y: number };
  raster_coordinate: { x: number; y: number };
}

const params = new URLSearchParams(window.location.search);
const mapInput = document.querySelector<HTMLInputElement>("#map-id")!;
const statusEl = document.querySelector("#status")!;
const detailEl = document.querySelector("#detail")!;
const form = document.querySelector<HTMLFormElement>("#map-form")!;

mapInput.value = params.get("map") || "842";

let leafletMap: L.Map | null = null;
const markers = new Map<string, L.Marker>();

function setStatus(text: string) {
  statusEl.textContent = text;
}

function pointIcon(point: PointItem): L.DivIcon | L.Icon {
  const url = point.labels[0]?.icon;
  if (url) {
    return L.icon({ iconUrl: url, iconSize: [28, 28], iconAnchor: [14, 14], className: "marker-icon" });
  }
  return L.divIcon({ className: "marker-icon", iconSize: [12, 12] });
}

async function openDetail(point: PointItem) {
  const payload = await fetch(`/api/v1/points/${point.id}`).then((r) => r.json());
  const detail = payload.detail || {};
  const images = (detail.images || [])
    .map((img: { url: string }) => `<img src="${img.url}" alt="" loading="lazy" />`)
    .join("");
  let body = "";
  if (detail.state === "EMPTY") body = "<p>该点位暂无官方详细说明</p>";
  else if (detail.state === "UNAVAILABLE") body = "<p>详细资料尚未同步</p>";
  else body = `<p>${detail.text || ""}</p>${images}`;
  detailEl.innerHTML = `<h2>${payload.labels.map((l: { name: string }) => l.name).join(" / ")}</h2>
    <p>map ${payload.core.map_id} · source ${payload.core.source_id}</p>${body}`;
  const next = new URL(window.location.href);
  next.searchParams.set("map", payload.core.map_id);
  next.searchParams.set("point", String(point.id));
  history.replaceState({}, "", next);
}

async function loadMap(mapId: string, selectedPoint?: string | null) {
  setStatus("loading");
  const info: MapInfo = await fetch(`/api/v1/maps/${mapId}`).then((r) => {
    if (!r.ok) throw new Error("map not found");
    return r.json();
  });
  const points: PointItem[] = await fetch(`/api/v1/maps/${mapId}/points`).then((r) => r.json());
  if (leafletMap) {
    leafletMap.remove();
    leafletMap = null;
  }
  markers.clear();
  const crs = createHsrCrs(info.origin[0], info.origin[1]);
  leafletMap = L.map("map", {
    crs,
    minZoom: -8,
    maxZoom: 4,
    zoomSnap: 0,
    zoomDelta: 0.5,
    zoomAnimation: false,
    fadeAnimation: false,
    markerZoomAnimation: false,
    attributionControl: false,
  });
  const overlay = addSingleImageRaster(leafletMap, info);
  const mapRef = leafletMap;
  let fitted = false;
  const fit = () => {
    if (fitted) return;
    fitted = true;
    applyKernelView(mapRef, info, points);
  };
  overlay.on("load", fit);
  requestAnimationFrame(() => requestAnimationFrame(fit));
  for (const point of points) {
    const marker = L.marker([point.y, point.x], { icon: pointIcon(point) });
    marker.on("click", () => openDetail(point));
    marker.addTo(leafletMap);
    markers.set(String(point.id), marker);
    markers.set(point.source_id, marker);
  }
  setStatus(`${info.name || info.id} · ${points.length} points`);
  const wanted = selectedPoint || new URLSearchParams(window.location.search).get("point");
  if (wanted) {
    const point = points.find((p) => String(p.id) === wanted || p.source_id === wanted);
    if (point) await openDetail(point);
  }
  const api = {
    ready: true,
    map: leafletMap,
    markers,
    points,
    info,
    measureGolden(items: GoldenItem[]) {
      if (!leafletMap) return { mean: 999, max: 999, errors: [] as number[] };
      const errors = items.map((item) => {
        const ll = L.latLng(item.source_coordinate.y, item.source_coordinate.x);
        const pt = leafletMap!.project(ll, 0);
        const dx = pt.x - item.raster_coordinate.x;
        const dy = pt.y - item.raster_coordinate.y;
        return Math.hypot(dx, dy);
      });
      return {
        mean: errors.reduce((a, b) => a + b, 0) / errors.length,
        max: Math.max(...errors),
        errors,
      };
    },
  };
  (window as unknown as { __HSRMAP: typeof api }).__HSRMAP = api;
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const mapId = mapInput.value.trim();
  const next = new URL(window.location.href);
  next.searchParams.set("map", mapId);
  next.searchParams.delete("point");
  history.replaceState({}, "", next);
  loadMap(mapId).catch((err) => {
    setStatus(String(err));
  });
});

loadMap(mapInput.value.trim(), params.get("point")).catch((err) => {
  setStatus(String(err));
});
