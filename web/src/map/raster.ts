import L from "leaflet";
import type { MapInfo } from "../api/types";
import { rasterLatLngBounds } from "./crs";

export function rasterUrl(info: MapInfo): string {
  if (info.raster.live_url && info.raster.asset) return `/api/v1/live-assets/${info.raster.asset}`;
  return `/assets/${info.raster.asset}`;
}

export function addSingleImageRaster(map: L.Map, info: MapInfo): L.ImageOverlay {
  const overlay = L.imageOverlay(rasterUrl(info), rasterLatLngBounds(info.bounds), {
    interactive: false,
    opacity: 1,
  });
  overlay.addTo(map);
  overlay.on("load", () => overlay.setOpacity(1));
  return overlay;
}

export function applyRasterView(map: L.Map, info: MapInfo): void {
  map.invalidateSize();
  map.setMaxBounds(rasterLatLngBounds(info.max_bounds));
  map.fitBounds(rasterLatLngBounds(info.bounds), { animate: false });
}
