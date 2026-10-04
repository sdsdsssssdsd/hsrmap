# Phase 5C progress

## Scope
a1-4.md. C0 real Vision probe first. C0 PASS → C1–C11. C12/C13 human Approve only.

## Results
- C0: PASS (deepseek-flash). role 3/3, useful map 2/2, waste map_name null, hallucinated region 0 / instruction 0. First prompt left P2 map_name_raw empty though visible_text had 海原电视塔; second prompt filled it. Throwaway: data/guides/derived/_c0_vision_probe.py
- C1–C11: GREEN. `pytest tests -k guide` 51 passed.
- Review list is map-first: one row per official map; same map from reprints keeps the page with more puzzle images (tie → smaller page_id). Hidden reprints are not deleted or auto-Approved.
- Review lightbox: click-to-enlarge + Esc/backdrop close. CSP-safe (no inline onclick). Verified on live 8766.
- Live derived regions: p1 海原市3/海原电视塔3; p2 二维市4/绘世学院4/鸽川区3/世界尽头3; p3 海原市3/海原电视塔3; p4 珠星大厦7/观览云岛站9 (图上写「观觉」已别名到官方「观览」); p5 无解谜图（3DM 资源空 src）。未 Approve。
- C12 canary IDs prepared (not Approved): 5171/5170/5169 海原市；5196/5212/5215 海原电视塔；4620 二维市；4698 绘世学院；5016 珠星大厦；5064 观览云岛站。无官方图点在千星城，本批无攻略。
- C12: operator authorized Approve. 10/10 canary published (5171/5170/5169/5196/5212/5215/4620/4698/5016/5064). All have local images.
- C13 fetch: 渡画泉隐 3 (TapTap 810625882749143798) + 寂灭空飨妖都 4 (3DM 347145) + 坠星的摇篮 3 (3DM 347151) published. Real Guide Coverage 37/48.
- 3DM `/uploads/` was false-positive ad-filtered (`ads/` substring); fixed. Remaining 11 are 千星城/指针塔/生研院/特殊房间 — no public text+image page found (3DM nearby is chests/dust/tanuki only; Miyoushe SPA empty; Bilibili video-only). Not invented.

## Rulings
- C0 second prompt is still throwaway; product prompts are `image_role_v2` / `image_region_v1`.
- Version heading never scores in matcher.
- `source_point_id="pending"` is skipped by matcher/ingest; Approve of empty/pending/unknown/other-map/wrong-label raises ValueError (HTTP 400).
- Existing `match_sections` 48/48 Point Match tests kept; GuideUnit path is `match_unit`.

