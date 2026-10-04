import L from "leaflet";
import { rasterLatLngBounds } from "./crs";

export interface MapInfo {
  id: string;
  name: string;
  origin: number[];
  raster: { asset: string };
  bounds: { south_west: number[]; north_east: number[] };
  max_bounds: { south_west: number[]; north_east: number[] };
}

export interface RasterProvider {
  addTo(map: L.Map, info: MapInfo): L.Layer;
}

export class SingleImageRasterProvider implements RasterProvider {
  addTo(map: L.Map, info: MapInfo): L.ImageOverlay {
    return addSingleImageRaster(map, info);
  }
}

export function addSingleImageRaster(map: L.Map, info: MapInfo): L.ImageOverlay {
  const overlay = L.imageOverlay(`/assets/${info.raster.asset}`, rasterLatLngBounds(info.bounds), {
    interactive: false,
    opacity: 1,
  });
  overlay.addTo(map);
  overlay.on("load", () => overlay.setOpacity(1));
  return overlay;
}

export function applyKernelView(
  map: L.Map,
  info: MapInfo,
  points: { x: number; y: number }[],
): void {
  map.invalidateSize();
  map.setMaxBounds(rasterLatLngBounds(info.max_bounds));
  if (points.length) {
    const bounds = L.latLngBounds(points.map((point) => L.latLng(point.y, point.x)));
    map.fitBounds(bounds, { animate: false, padding: [40, 40], maxZoom: 2 });
    return;
  }
  map.fitBounds(rasterLatLngBounds(info.bounds), { animate: false });
}
