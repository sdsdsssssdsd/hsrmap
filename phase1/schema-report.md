# HSR HoYoLAB Map Phase 1 Schema Report

Generated at `2026-10-01T09:44:29.155004+00:00`.

This report freezes the public protocol observed from the current official page.
It is not a product spec and does not start Full Downloader.

## Frontend identity

- entry: `https://act.hoyolab.com/sr/app/interactive-map/index.html?lang=zh-cn`
- bundle_url: `https://act.hoyolab.com/sr/app/interactive-map/bundle_b1c3be6a5806caef8417.js`
- bundle_sha256: `e5cefc8fccf6e5473d056e37f8a48636dbfc85d411d7d2a2142b932f58afdd1c`
- app_version (API client param, not frontend version): `de16a09fca4e0ab89acf69fe0c12514f`
- public host: `https://sg-act-public-api-static.hoyolab.com/common/srmap/sr_map`
- authenticated host: `https://sg-act-public-api.hoyolab.com/common/srmap/sr_map`

A later `app_version` match does **not** mean the frontend is unchanged.
Compare `bundle_url`, `bundle_sha256`, public host, and schema fingerprints.

## Shared query parameters actually used

Successful anonymous GETs used:

```text
app_sn=sr_map
lang=zh-cn
app_version=de16a09fca4e0ab89acf69fe0c12514f
```

No Cookie / Authorization header was sent.
The frontend also attaches `x-rpc-lrsag`, but Phase 1 requests succeeded without it.

## /v1/map/tree

- method: `GET`
- auth: `none`
- anonymous success: `True`
- retcode/message: `0 / OK`
- parameters: `map_id, app_sn, lang, app_version`
- required headers: none beyond User-Agent
- schema fingerprint: `aff06cb3fb0cf79b1ad2879b94ab6c848e4ac6efcb2f02ef1db8a8bd52d2b509`
- notes: Returns the full HSR map tree. `map_id` is accepted; Phase 1 used the current test map id after confirming the tree is not a single-map slice.

JSON paths:

```text
$.data
$.data.tree
$.data.tree[]
$.data.tree[].children
$.data.tree[].children[]
$.data.tree[].children[].children
$.data.tree[].children[].children[]
$.data.tree[].children[].children[].children
$.data.tree[].children[].children[].depth
$.data.tree[].children[].children[].icon
$.data.tree[].children[].children[].id
$.data.tree[].children[].children[].is_hide
$.data.tree[].children[].children[].map_group_type
$.data.tree[].children[].children[].map_shape
$.data.tree[].children[].children[].name
$.data.tree[].children[].children[].node_type
$.data.tree[].children[].children[].parent_id
$.data.tree[].children[].children[].preview
$.data.tree[].children[].children[].related_group_map
$.data.tree[].children[].children[].related_id
$.data.tree[].children[].depth
$.data.tree[].children[].icon
$.data.tree[].children[].id
$.data.tree[].children[].is_hide
$.data.tree[].children[].map_group_type
$.data.tree[].children[].map_shape
$.data.tree[].children[].name
$.data.tree[].children[].node_type
$.data.tree[].children[].parent_id
$.data.tree[].children[].preview
$.data.tree[].children[].related_group_map
$.data.tree[].children[].related_id
$.data.tree[].depth
$.data.tree[].icon
$.data.tree[].id
$.data.tree[].is_hide
$.data.tree[].map_group_type
$.data.tree[].map_shape
$.data.tree[].name
$.data.tree[].node_type
$.data.tree[].parent_id
$.data.tree[].preview
$.data.tree[].related_group_map
$.data.tree[].related_id
$.message
$.retcode
```

## /v1/map/info

- method: `GET`
- auth: `none`
- anonymous success: `True`
- retcode/message: `0 / OK`
- parameters: `map_id, app_sn, lang, app_version`
- required headers: none beyond User-Agent
- schema fingerprint: `d411fb3cb75c5e2878747ca8108051244926cc719bd6c0eb2b2449b60a52b603`
- notes: One node. `detail` is a JSON string containing origin/padding/total_size/slices.

JSON paths:

```text
$.data
$.data.info
$.data.info.ch_ext
$.data.info.children
$.data.info.depth
$.data.info.detail
$.data.info.game_map_id
$.data.info.icon
$.data.info.id
$.data.info.is_hide
$.data.info.map_all_treasure_count
$.data.info.map_group_type
$.data.info.map_shape
$.data.info.name
$.data.info.node_type
$.data.info.parent_id
$.data.info.parent_name
$.data.info.preview
$.data.info.related_group_map
$.data.info.related_id
$.data.info.sort
$.message
$.retcode
```

## /v1/map/label/tree

- method: `GET`
- auth: `none`
- anonymous success: `True`
- retcode/message: `0 / OK`
- parameters: `map_id, app_sn, lang, app_version`
- required headers: none beyond User-Agent
- schema fingerprint: `c3a38fee9dce347c8321e03d50535f418a503d93ebebdcdf92f66182dfc7a17d`
- notes: Category tree. Depth 1 = groups, deeper nodes = selectable labels.

JSON paths:

```text
$.data
$.data.tree
$.data.tree[]
$.data.tree[].activity_page_label
$.data.tree[].area_page_label
$.data.tree[].ch_ext
$.data.tree[].children
$.data.tree[].children[]
$.data.tree[].children[].activity_page_label
$.data.tree[].children[].area_page_label
$.data.tree[].children[].ch_ext
$.data.tree[].children[].children
$.data.tree[].children[].depth
$.data.tree[].children[].display_priority
$.data.tree[].children[].icon
$.data.tree[].children[].id
$.data.tree[].children[].is_all_area
$.data.tree[].children[].jump_target_id
$.data.tree[].children[].jump_type
$.data.tree[].children[].label_description
$.data.tree[].children[].mark_status
$.data.tree[].children[].name
$.data.tree[].children[].node_type
$.data.tree[].children[].parent_id
$.data.tree[].children[].sort
$.data.tree[].children[].strategy
$.data.tree[].depth
$.data.tree[].display_priority
$.data.tree[].icon
$.data.tree[].id
$.data.tree[].is_all_area
$.data.tree[].jump_target_id
$.data.tree[].jump_type
$.data.tree[].label_description
$.data.tree[].mark_status
$.data.tree[].name
$.data.tree[].node_type
$.data.tree[].parent_id
$.data.tree[].sort
$.data.tree[].strategy
$.message
$.retcode
```

## /v1/map/point/list

- method: `GET`
- auth: `none`
- anonymous success: `True`
- retcode/message: `0 / OK`
- parameters: `map_id, app_sn, lang, app_version`
- required headers: none beyond User-Agent
- schema fingerprint: `373c1de976648934671950a71f8203acd424e4c705faedf31b615addf9627d36`
- notes: Point Core only: ids, label_id, x_pos/y_pos. No description/image.

JSON paths:

```text
$.data
$.data.label_list
$.data.label_list[]
$.data.label_list[].activity_page_label
$.data.label_list[].area_page_label
$.data.label_list[].ch_ext
$.data.label_list[].children
$.data.label_list[].depth
$.data.label_list[].display_priority
$.data.label_list[].icon
$.data.label_list[].id
$.data.label_list[].is_all_area
$.data.label_list[].jump_target_id
$.data.label_list[].jump_type
$.data.label_list[].label_description
$.data.label_list[].mark_status
$.data.label_list[].name
$.data.label_list[].node_type
$.data.label_list[].parent_id
$.data.label_list[].sort
$.data.label_list[].strategy
$.data.point_list
$.data.point_list[]
$.data.point_list[].author_name
$.data.point_list[].ctime
$.data.point_list[].day_night_status
$.data.point_list[].display_state
$.data.point_list[].id
$.data.point_list[].label_id
$.data.point_list[].point_num
$.data.point_list[].related_jump_id
$.data.point_list[].video_url
$.data.point_list[].x_pos
$.data.point_list[].y_pos
$.message
$.retcode
```

## /v1/map/point/info

- method: `GET`
- auth: `none`
- anonymous success: `True`
- retcode/message: `0 / OK`
- parameters: `point_id, app_sn, lang, app_version`
- required headers: none beyond User-Agent
- schema fingerprint: `ed105c4047722f6dd0a22a7130ea13b704d76a9f8d2c394c0e209bb411630a9f`
- notes: Point Detail: content, img, map_id. Phase 1 fetches only the 20 acceptance points.

JSON paths:

```text
$.data
$.data.correct_user_list
$.data.info
$.data.info.author
$.data.info.author_name
$.data.info.content
$.data.info.ctime
$.data.info.day_night_status
$.data.info.display_state
$.data.info.editor
$.data.info.editor_name
$.data.info.expansion
$.data.info.id
$.data.info.img
$.data.info.label_id
$.data.info.map_id
$.data.info.point_num
$.data.info.record_id
$.data.info.related_jump_id
$.data.info.url_list
$.data.info.version
$.data.info.video_url
$.data.info.x_pos
$.data.info.y_pos
$.data.last_update_time
$.message
$.retcode
```

## map/tree node classification

Do not call every tree node a map. Observed fields: `id`, `name`, `parent_id`, `depth`, `node_type`, `children`, `icon`, `preview`, `is_hide`, `map_shape`, `map_group_type`, `related_id`, `related_group_map`.

The tree itself does **not** include raster `detail`. That only appears on `map/info`.

- total tree nodes: **923**
- nodes with children / folders: **299**
- leaf nodes: **624**
- nodes with id: **923**
- node_type counts: `{1: 299, 2: 624}`
- map/info probed (node_type==2): **624** succeeded
- map/info with raster detail: **624**
- leftover empty leaves after probe: **0**

Renderable map rule used here:

```text
node_type == 2
AND map/info.retcode == 0
AND parsed detail.slices contains at least one url
```

## label/tree

- label nodes walked: **1016**
- category / depth-1: **10**
- selectable / no children: **1006**

Categories:

- `23` 地标
- `16` 战利品
- `1` 阅读物
- `21` 敌人
- `22` 裂界
- `18` 历战余响
- `394` 活动
- `656` 显性战利品
- `657` 隐性战利品
- `658` 解密战利品

Semantic labels resolved by **name**, not hardcoded IDs:

- `floating_grease_origin_retrace` → `浮脂溯源·二次元ROTATE！` observed_source_id=`686`
- `floating_grease_notes` → `「浮脂记事」` observed_source_id=`842`

## Point Core vs Point Detail

Point Core comes from `/v1/map/point/list` and includes `id`, `label_id`, `x_pos`, `y_pos` and related display fields. 海原市 list length: **48**.

Point Detail comes from `/v1/map/point/info` and adds `map_id`, `content`, `img`, `url_list`.
These must stay separate pipelines after Phase 1.
Phase 1 fetched 20 `point/info` samples; 19 contain `content`/`img`, point `5260` returned empty detail fields. The raw JSON is still kept.

## Test map raster

- name: `海原市`
- source id: `842`
- canvas: `8192 × 4096`
- fragments: **1**
- origin: `[3827, 2217]`
- padding: `[1706, 219]`

Official tile layer divides `total_size` by slice rows/cols. A single full-map slice is a valid RasterSpec fragment, not a 2048 grid.

## Coordinate transform

From the current official bundle CRS factory:

```text
L.marker([y_pos, x_pos])
project(latlng) = Point(lng + origin_x, lat + origin_y)
transformation = (1, 0, 1, 0)  # no Y flip
raster_x = x_pos + origin_x
raster_y = y_pos + origin_y
```

padding is used only for Leaflet maxBounds, not marker placement.

```json
{
  "map_id": 842,
  "source": "hoyolab-hsr",
  "transform": {
    "type": "affine",
    "scale_x": 1.0,
    "scale_y": 1.0,
    "offset_x": 3827.0,
    "offset_y": 2217.0,
    "flip_x": false,
    "flip_y": false
  },
  "source_origin": [
    3827.0,
    2217.0
  ],
  "notes": "Official bundle CRS: raster_x = x_pos + origin_x; raster_y = y_pos + origin_y. Leaflet marker is [y_pos, x_pos]. padding is not part of the point transform."
}
```

## Alignment

Independent reference is official Leaflet `map.project(latlng, 0)` on `#/map/842`.
All 20 Phase 1 points matched a live marker (`live_leaflet_matches = 20`).

- points: 20
- mean error: 0.0 px
- max error: 0.0 px
- passed: True

