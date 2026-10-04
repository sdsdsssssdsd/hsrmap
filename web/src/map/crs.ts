import L from "leaflet";

export function createHsrCrs(originX: number, originY: number): L.CRS {
  return L.Util.extend({}, L.CRS.Simple, {
    projection: L.Projection.LonLat,
    transformation: new L.Transformation(1, originX, 1, originY),
    infinite: true,
  });
}

export function rasterLatLngBounds(bounds: { south_west: number[]; north_east: number[] }): L.LatLngBounds {
  return L.latLngBounds(
    L.latLng(bounds.south_west[0], bounds.south_west[1]),
    L.latLng(bounds.north_east[0], bounds.north_east[1]),
  );
}
