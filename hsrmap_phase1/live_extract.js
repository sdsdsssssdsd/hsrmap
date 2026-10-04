() => {
  const vue = document.querySelector("#root").__vue__;
  const seen = new Set();
  const queue = [vue];
  let map = null;
  while (queue.length && !map) {
    const cur = queue.shift();
    if (!cur || typeof cur !== "object" || seen.has(cur)) continue;
    seen.add(cur);
    if (typeof cur.project === "function" && typeof cur.eachLayer === "function" && cur._layers) {
      map = cur;
      break;
    }
    if (cur.map && typeof cur.map.project === "function" && cur.map.eachLayer) {
      map = cur.map;
      break;
    }
    if (Array.isArray(cur.$children)) {
      for (let i = 0; i < cur.$children.length; i += 1) queue.push(cur.$children[i]);
    }
    if (cur.$parent) queue.push(cur.$parent);
    if (cur.map) queue.push(cur.map);
    if (cur.gameMap) queue.push(cur.gameMap);
  }
  if (!map) return { error: "no-map" };
  const out = {};
  const sample = [];
  map.eachLayer((layer) => {
    if (!layer.getLatLng) return;
    const ll = layer.getLatLng();
    const pt0 = map.project(ll, 0);
    const rec = {
      pointId: layer.pointId || null,
      pointNum: layer.pointNum || null,
      lat: ll.lat,
      lng: ll.lng,
      x0: pt0.x,
      y0: pt0.y,
    };
    if (sample.length < 8) sample.push(rec);
    if (rec.pointId != null) out[String(rec.pointId)] = [pt0.x, pt0.y, ll.lat, ll.lng];
  });
  return { count: Object.keys(out).length, sample, zoom: map.getZoom(), points: out };
}
