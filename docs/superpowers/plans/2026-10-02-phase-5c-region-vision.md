# Phase 5C Region Vision & Point Binding V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace version-title matching with image-region → official map_id → grease candidates → suggested source_point_id; only human Approve publishes.

**Architecture:** Frozen G0–G4 stay. New vision/region/unit/candidate units feed Matcher V2. Vision results live in derived JSON. Review items bind GuideUnits. Empty or foreign point IDs cannot Approve.

**Tech Stack:** Python, Fake/DeepSeek providers, pytest fixtures, existing FastAPI review console, official core.db read-only.

**Spec:** `a1-4.md`

## Global Constraints

- Personal local use. Do not redistribute third-party article text/images.
- Rate-limited, robots-aware, no captcha/proxy/login bypass.
- Tests never require live sites or DeepSeek. Use fixtures / Fake provider.
- Official core.db / detail.db / data/assets stay read-only.
- AUTO_SUGGEST is not published. Real Guide Coverage stays 0 until human Approve.
- Never write source_point_id `"pending"`.
- Version numbers never contribute map or point score.
- Viewer CSP unchanged. Keys only from env / gitignored `.env`.
- C12/C13 (10-point and 48-point publish) wait for human Approve. Do not fake Approve.

## Review Focus

- Waste images must not invent 海原市.
- `二相乐园` resolves as parent, not a renderable map.
- Steps on unit B must not copy unit A.
- Approve of empty / other-map / wrong-label IDs is rejected server-side.
- Missing DEEPSEEK_API_KEY does not crash ingest.

---

## Task C0 — Real 3-image Vision Probe

**Files:** `data/guides/derived/_c0_vision_probe.py` (throwaway); `data/guides/derived/_c0_vision_probe.json`

**Produces:** Honest PASS/FAIL. Not pytest.

P1 = 海原市 composite (`51c76fca…png`). P2 = 海原电视塔 composite (`b59b46a6…png`). P3 = subway ad (`7cbea94e…jpg`).

PASS: role 3/3, useful map 2/2, waste map_name null, hallucinated region 0, hallucinated instruction 0.

If FAIL: stop product rewrite; swap vision model/provider only.

- [ ] Run throwaway probe with live key
- [ ] Record result in ledger
- [ ] Do not commit the JSON if it embeds prompts with secrets

---

## Task C1 — Vision Provider sends image bytes

**Files:** `hsrmap/guides/llm/provider.py`; `hsrmap/guides/llm/fake.py`; `hsrmap/guides/llm/deepseek.py`; `tests/test_guide_vision_provider.py`

**Produces:** `classify_image` / `read_region` / `compare_candidates` take bytes+mime+sha. Fake never networks. DeepSeek builds `image_url` data URL.

### RED

```python
from hsrmap.guides.llm.deepseek import DeepSeekGuideLLMProvider

def test_classify_image_includes_data_url(monkeypatch):
    seen = {}
    def fake_json(self, **kwargs):
        seen.update(kwargs)
        return {"role": "puzzle_step", "confidence": 0.9}
    monkeypatch.setattr(DeepSeekGuideLLMProvider, "_vision_json", fake_json)
    p = DeepSeekGuideLLMProvider({"endpoint": "https://example.invalid", "vision_model": "deepseek-flash"}, "k")
    p.classify_image(image_bytes=b"png", mime="image/png", sha256="aa")
    assert seen["image_b64"]
```

Expected: FAIL — `_vision_json` or image_b64 missing.

### GREEN

Implement `_vision_json` that puts `image_url` in the user content array. `classify_image` without bytes returns `unknown`.

---

## Task C2 — Image Role V2

**Files:** `hsrmap/guides/vision/roles.py`; `hsrmap/guides/prompts/image_role_v2.txt`; `tests/test_guide_image_role.py`

Allowed roles: `location_map puzzle_step route_map reward cover advertisement unrelated unknown`.

Fake maps sha → role. Deterministic prefilter (tiny file, QR alt, known ad URL) skips API.

### RED

```python
from hsrmap.guides.vision.roles import classify_role

def test_waste_qr_is_unrelated_without_provider():
    assert classify_role(b"x", mime="image/webp", sha256="q", alt="17173APP", src="...wx.webp", provider=None)["role"] == "unrelated"
```

---

## Task C3 — Region Reader

**Files:** `hsrmap/guides/vision/region_reader.py`; `hsrmap/guides/prompts/image_region_v1.txt`; `tests/test_guide_region_reader.py`

Only useful roles. Model returns raw text only. `4.2版本` → `map_name_raw=null`.

### RED

```python
from hsrmap.guides.vision.region_reader import read_region

def test_version_banner_is_not_a_map():
    class Fake:
        def read_region(self, **kwargs):
            return {"map_name_raw": "4.2版本", "visible_text": ["4.2版本"], "grounded": True}
    out = read_region(b"x", mime="image/png", sha256="v", provider=Fake(), whitelist=["海原市"])
    assert out["map_name_raw"] is None
```

---

## Task C4 — OfficialMapResolver

**Files:** `hsrmap/guides/regions/resolver.py`; `tests/test_guide_map_resolver.py`

exact → normalized exact → alias exact → unique fuzzy → AMBIGUOUS/NO_MATCH. `二相乐园` = parent, `map_id=null`. Duplicate floor names AMBIGUOUS without parent.

### RED

```python
from hsrmap.guides.regions.resolver import resolve_map

def test_erxiang_is_parent_not_renderable():
    maps = [{"map_id": "root", "name": "二相乐园", "renderable": False, "path": "二相乐园"}]
    out = resolve_map("二相乐园", maps)
    assert out["status"] == "PARENT" and out["map_id"] is None
```

---

## Task C5 — RegionSectionBuilder

**Files:** `hsrmap/guides/regions/sections.py`; `tests/test_guide_region_sections.py`

3 海原市 + 2 海原电视塔 + 1 ad → 2 sections, ad excluded. Version heading score 0. Propagation marked `propagated_from_previous_region_anchor`.

---

## Task C6 — GuideUnitBuilder

**Files:** `hsrmap/guides/regions/units.py`; `tests/test_guide_units.py`

海原市 location+steps ×3 → 3 units, not one mega-guide. No ordinal/location_map → `UNRESOLVED_REGION_BATCH`.

---

## Task C7 — OfficialCandidateQuery

**Files:** `hsrmap/guides/matching/candidates.py`; `tests/test_guide_candidates.py`

海原市 + floating grease → only that map's official points (fixture 3). 0 candidates stays at region layer.

---

## Task C8 — Matcher V2 + ingest orchestration

**Files:** `hsrmap/guides/matching/matcher.py`; `hsrmap/guides/matching/evidence.py`; `hsrmap/guides/ingest.py`; `tests/test_guide_match.py`; `tests/test_guide_ingest.py`

No `pending`. Version heading does not score. Each review item uses its own unit steps (no first-section leak). AUTO_SUGGEST vs NEEDS_REVIEW per a1-4 thresholds. Missing key: ingest lives, roles unknown.

### RED

```python
def test_ingest_never_writes_pending(tmp_path):
    ...
    assert all(item["source_point_id"] != "pending" for item in result["review"])
```

---

## Task C9 — Official image similarity (pHash) + compare slot

**Files:** `hsrmap/guides/matching/image_similarity.py`; `tests/test_guide_image_similarity.py`

pHash first. Vision compare only among candidate ids. OpenCV remains auxiliary, never sole publisher.

---

## Task C10 — Review V2

**Files:** `hsrmap/guides/review/service.py`; `console.html`; `console.js`; `tests/test_guide_review_api.py`

Left: group by map_name. Right: useful images default, waste folded. Center: candidate dropdown only. draft schema_version 2 with unit images/steps.

---

## Task C11 — Server Approval Guard

**Files:** `hsrmap/guides/review/service.py`; `tests/test_guide_approve_guard.py`

Reject: empty id, unknown id, other-map id, wrong semantic label. Accept: id in unit candidates.

### RED

```python
def test_approve_empty_id_rejected(db):
    item = create_item(db, {"page_id": 1, "source_point_id": "", "status": "AUTO_SUGGEST", "draft": {}})
    try:
        approve_item(db, item["id"])
    except Exception as exc:
        assert "source_point_id" in str(exc).lower()
    else:
        raise AssertionError("empty id must not approve")
```

---

## Task C12 / C13 — Human canary (do not implement Approve)

Stop after C11. Operator Approves 10 diverse points. Do not auto-approve. Remaining 38 stay BLOCKED until 10/10 canary PASS.

---

## Self-review

- C0–C11 cover a1-4 identity chain and the two integrity fixes (`pending`, server Approve).
- No TBD steps.
- Review Focus mapped to C2/C4/C8/C11/C8-no-key.
- C12/C13 explicitly human-gated.
