let current = null;

async function loadGates() {
  const box = document.getElementById("gate-box");
  if (!box) return;
  try {
    const body = await (await fetch("/api/v1/atlas/gates")).json();
    const lines = ["Engine Gate  " + (body.result || "") + " · Wave " + (body.wave || "")];
    ["A", "B", "C"].forEach((key) => {
      const gate = body[key] || {};
      lines.push("Gate " + key + " — " + (gate.name || "") + "  " + (gate.result || ""));
      (gate.topics || []).forEach((row) => {
        lines.push("  " + (row.display || row.topic) + "  " + (row.engine || "") +
          (row.official != null ? ("  published " + (row.published || 0) + "/" + (row.official || 0)) : "") +
          (row.next ? "  next: " + row.next : ""));
      });
    });
    box.textContent = lines.join("\n");
  } catch (err) {
    box.textContent = "Gate 看板加载失败";
  }
}

async function loadTopics() {
  const select = document.getElementById("topic");
  if (!select || select.options.length > 1) return;
  try {
    const body = await (await fetch("/api/v1/guides/topics")).json();
    (body.topics || []).forEach((topic) => {
      const opt = document.createElement("option");
      opt.value = topic.topic_key;
      opt.textContent = topic.display_name || topic.topic_key;
      select.appendChild(opt);
    });
    select.onchange = () => load();
  } catch (err) {
    return;
  }
}

async function load() {
  const count = document.getElementById("count");
  const list = document.getElementById("list");
  try {
    const topic = (document.getElementById("topic") || {}).value || "";
    const query = topic ? ("?topic=" + encodeURIComponent(topic)) : "";
    const body = await (await fetch("/api/v1/review/items" + query)).json();
    const maps = (body.maps || []).length ? body.maps : (body.items || []).map(itemRow);
    const canaryN = maps.reduce((n, row) => n + (row.slots || []).filter((s) => s.canary).length, 0);
    count.textContent = maps.length
      ? ("按地图 " + maps.length + " 处，同地区一份 · C12 Canary " + canaryN + "/10")
      : "队列是空的";
    list.innerHTML = "";
    maps.forEach((row) => {
      const div = document.createElement("div");
      div.className = "item";
      const extra = row.hidden ? (" · 已折叠 " + row.hidden + " 份转载") : "";
      const slots = row.slots || [];
      const canary = slots.filter((s) => s.canary).length;
      div.innerHTML = "<strong>" + row.map_name + " · " + imageCount(row) + "张</strong><small>" +
        (row.page_title || ("page " + row.page_id)) + extra +
        (slots.length ? " · 官方 " + slots.length + " 点" : "") +
        (canary ? " · C12 " + canary : "") + "</small>";
      div.onclick = () => openRow(row, div);
      list.appendChild(div);
    });
  } catch (err) {
    count.className = "err";
    count.textContent = "列表加载失败。不要打开 /api/v1/review/items，请用本页 /review。";
  }
}

function itemRow(item) {
  const draft = item.draft || {};
  const name = (draft.resolved_map_name || draft.map_name || item.page_title || ("page " + item.page_id)).split(" / ")[0];
  const images = ((item.image_groups || {})["未识别地区"] || []).filter(Boolean);
  return {
    map_name: name,
    page_id: item.page_id,
    item_id: item.id,
    images,
    waste: (item.image_groups || {})["废图"] || [],
    hidden: 0,
    status: item.status,
    page_title: item.page_title,
    source_point_id: item.source_point_id || "",
    draft,
    evidence: item.evidence || draft.evidence || {},
    layout: item.layout,
    layout_html: item.layout_html,
    candidate_points: draft.candidate_points || [],
    slots: (draft.candidate_points || []).map((row) => ({source_point_id: row.source_point_id, canary: false})),
  };
}

function imageCount(row) {
  if (typeof row.image_count === "number") return row.image_count;
  return (row.images || []).length;
}

//: 列表接口默认是「索引」（不带 draft / 布局 / 图片数组），选中时再按行取详情。
//: 取不到就退回用索引那一行，至少不会点不动。
async function openRow(row, el) {
  if (!row || row.layout || !row.item_id) {
    select(row, el);
    return;
  }
  try {
    const full = await (await fetch("/api/v1/review/maps/" + row.item_id)).json();
    select(Object.assign({}, row, full), el);
  } catch (err) {
    select(row, el);
  }
}

function select(row, el) {
  current = row;
  document.querySelectorAll(".item").forEach((n) => n.classList.remove("active"));
  el.classList.add("active");
  const point = document.getElementById("point");
  const selectEl = document.getElementById("candidates");
  const cands = row.candidate_points || (row.draft || {}).candidate_points || [];
  const firstCanary = (row.slots || []).find((s) => s.canary);
  point.value = row.source_point_id || (firstCanary && firstCanary.source_point_id) || "";
  selectEl.innerHTML = "";
  if (cands.length) {
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "选择官方候选";
    selectEl.appendChild(empty);
    cands.forEach((cand) => {
      const opt = document.createElement("option");
      opt.value = cand.source_point_id;
      opt.textContent = cand.source_point_id + (cand.canary ? " · C12" : "") + (cand.map_path ? " · " + cand.map_path : "");
      if (String(cand.source_point_id) === String(point.value)) opt.selected = true;
      selectEl.appendChild(opt);
    });
    selectEl.onchange = () => { point.value = selectEl.value; };
    selectEl.style.display = "block";
    point.readOnly = true;
    const officialBox = document.getElementById("official-cands");
    if (officialBox) {
      officialBox.innerHTML = "";
      const head = document.createElement("div");
      head.className = "group";
      head.textContent = "官方候选图";
      officialBox.appendChild(head);
      cands.forEach((cand) => {
        if (!cand.official_image_url) return;
        const wrap = document.createElement("div");
        wrap.style.display = "inline-block";
        wrap.style.margin = "4px";
        const label = document.createElement("div");
        label.textContent = String(cand.source_point_id || "");
        const img = document.createElement("img");
        img.src = cand.official_image_url;
        img.alt = String(cand.source_point_id || "official");
        img.title = "点击选为 HUMAN_REVIEW";
        img.style.maxWidth = "160px";
        img.onclick = () => {
          point.value = cand.source_point_id;
          selectEl.value = cand.source_point_id;
        };
        wrap.appendChild(img);
        wrap.appendChild(label);
        officialBox.appendChild(wrap);
      });
    }
  } else {
    selectEl.style.display = "none";
    point.readOnly = false;
    const officialBox = document.getElementById("official-cands");
    if (officialBox) officialBox.innerHTML = "";
  }
  document.getElementById("steps").value = JSON.stringify((row.draft || {}).steps || [], null, 2);
  document.getElementById("evidence").textContent = JSON.stringify(row.evidence || {}, null, 2);
  const draft = row.draft || {};
  const targetType = document.getElementById("target-type");
  const targetKey = document.getElementById("target-key");
  if (targetType) targetType.value = draft.target_type || "";
  if (targetKey) targetKey.value = draft.target_key || "";
  const preview = document.getElementById("preview");
  if (preview) preview.innerHTML = row.layout_html || JSON.stringify(row.layout || {}, null, 2);
  document.getElementById("meta").textContent = (row.status || "") + " · " + row.map_name + " · page " + row.page_id;
  const box = document.getElementById("images");
  const wasteBox = document.getElementById("waste");
  const note = document.getElementById("image-note");
  box.innerHTML = "";
  wasteBox.innerHTML = "";
  const images = row.images || [];
  note.textContent = row.map_name + " " + images.length + "张" + (row.hidden ? "；转载已折叠 " + row.hidden + " 份" : "");
  const head = document.createElement("div");
  head.className = "group";
  head.textContent = row.map_name + " · " + images.length;
  box.appendChild(head);
  images.forEach((img) => box.appendChild(thumb(img)));
  const waste = row.waste || [];
  if (waste.length) {
    const whead = document.createElement("div");
    whead.className = "group";
    whead.textContent = "废图已排除 · " + waste.length;
    wasteBox.appendChild(whead);
    waste.forEach((img) => wasteBox.appendChild(thumb(img)));
  }
}

function thumb(img) {
  const el = document.createElement("img");
  el.src = img.url;
  el.alt = img.alt || img.sha || "";
  el.title = ((img.map_name || "") + " " + (img.role || "")).trim() || "点击放大";
  el.onclick = (ev) => { ev.stopPropagation(); openLightbox(img); };
  return el;
}

function openLightbox(img) {
  const box = document.getElementById("lightbox");
  document.getElementById("lightbox-img").src = img.url;
  document.getElementById("lightbox-cap").textContent = [img.map_name, img.role, "点击空白关闭"].filter(Boolean).join(" · ");
  box.classList.add("open");
}

function closeLightbox() {
  document.getElementById("lightbox").classList.remove("open");
  document.getElementById("lightbox-img").src = "";
}

document.getElementById("lightbox").addEventListener("click", closeLightbox);
document.getElementById("lightbox-img").addEventListener("click", (ev) => ev.stopPropagation());
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") closeLightbox();
});

document.getElementById("save").onclick = async () => {
  if (!current) return;
  const steps = JSON.parse(document.getElementById("steps").value || "[]");
  await fetch("/api/v1/review/items/" + current.item_id, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ source_point_id: document.getElementById("point").value, binding_method: "HUMAN_REVIEW", draft: { steps } })
  });
  await load();
};
document.getElementById("approve").onclick = async () => {
  if (!current) return;
  const pid = document.getElementById("point").value;
  if (!pid || pid === "pending") {
    alert("空的或 pending 的 source_point_id 不能 Approve");
    return;
  }
  let itemId = current.item_id;
  if (!itemId) {
    const created = await (await fetch("/api/v1/review/items", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        page_id: current.page_id,
        source_point_id: pid,
        status: "NEEDS_REVIEW",
        draft: { map_name: current.map_name, candidate_points: current.candidate_points || [], steps: JSON.parse(document.getElementById("steps").value || "[]") }
      })
    })).json();
    itemId = created.id;
  } else {
    await fetch("/api/v1/review/items/" + itemId, {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ source_point_id: pid, draft: { map_name: current.map_name, candidate_points: current.candidate_points || [] } })
    });
  }
  const resp = await fetch("/api/v1/review/items/" + itemId + "/approve", { method: "POST" });
  if (!resp.ok) {
    const text = await resp.text();
    alert("Approve 被后端拒绝：" + text);
    return;
  }
  await load();
};
document.getElementById("reject").onclick = async () => {
  if (!current) return;
  await fetch("/api/v1/review/items/" + current.item_id + "/reject", { method: "POST" });
  await load();
};
loadGates();
loadTopics().then(load);
