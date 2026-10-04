# Phase 1 README

This directory freezes the current HoYoLAB HSR public map protocol and proves one map + 20 points.

## Result

```text
Bundle:
e5cefc8fccf6e5473d056e37f8a48636dbfc85d411d7d2a2142b932f58afdd1c

Public API host:
https://sg-act-public-api-static.hoyolab.com/common/srmap/sr_map

Map tree:
923 raw nodes
624 renderable maps
299 folders/groups
0 unsupported/empty leaves

Labels:
1016 raw label nodes

Test map:
海原市
canvas: 8192 × 4096
fragments: 1

Test points:
20

Transform:
raster_x = x_pos + origin_x
raster_y = y_pos + origin_y

Mean alignment error:
0.00 px  (vs live official Leaflet map.project, 20/20 matches)

Max alignment error:
0.00 px

Floating Grease:
semantic key resolved
source label id: 686 at this snapshot

Floating Grease Notes:
semantic key resolved
source label id: 842 at this snapshot

Phase 1:
PASS
```

Replay:

```text
python -m hsrmap_phase1.run
```
