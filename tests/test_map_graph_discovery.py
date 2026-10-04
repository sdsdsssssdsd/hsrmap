"""M7.2 发现层（a1-8-1 §七 / §八）：五个 extractor + ID Reference Scanner + 闭包收敛。

覆盖四件事：

1. **每个 extractor 的字段解析**：哪个键、哪个 json 路径、产出什么边（discovery_source 可追）；
2. **scanner 的过滤与「不猜」**：`0` / `"0"` / `""` 一律当「无」；id 空间重叠时**必须探测**，
   不能按数值区间判语义；
3. **闭包收敛**：用 M7.1 的 `closure()` 跑 M7.2 的 extractor，frontier 收敛、cycle 不死循环、
   拿不到证据就不假装收敛；
4. 键语义注册表（transition registry）与 bundle 契约扫描。
"""

from __future__ import annotations

import pytest

from hsrmap.discovery import (
    ACTION_LABEL_REDIRECT,
    MAP_LIKE_NON_RASTER,
    NOT_A_MAP,
    PROBE_MAP_LIKE,
    PROBE_NOT_MAP,
    PROBE_RENDERABLE,
    PROBE_UNKNOWN,
    SCAN_CONCLUSIONS,
    SPACE_LABEL,
    SPACE_MAP,
    UNKNOWN_RENDERABLE_TARGET,
    UNRESOLVED,
    aggregate_scans,
    as_map_id,
    bundle_key_contracts,
    contract_for_key,
    expand_id_values,
    extract_bundle_contract,
    extract_map_info,
    extract_point_info,
    extract_point_list,
    extract_tree,
    is_absent,
    is_reference_key,
    scan_id_references,
)
from hsrmap.discovery import MapProbeResult
from hsrmap.graph import (
    MAP_GROUP,
    POINT_JUMP,
    RELATED_MAP,
    RETURN,
    TREE_CHILD,
    closure,
)

# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def sample_tree() -> dict:
    """官方 map/tree 的骨架：根 → 容器 → 两张地图 + 隐藏容器（related / group）。"""
    return {
        "retcode": 0,
        "message": "OK",
        "data": {
            "tree": [
                {
                    "id": 1,
                    "name": "二相乐园",
                    "node_type": 1,
                    "depth": 1,
                    "parent_id": 0,
                    "is_hide": False,
                    "related_id": "0",
                    "related_group_map": 0,
                    "children": [
                        {
                            "id": 10,
                            "name": "海原市",
                            "node_type": 1,
                            "depth": 2,
                            "parent_id": 1,
                            "is_hide": False,
                            "related_id": "0",
                            "related_group_map": 0,
                            "children": [
                                {
                                    "id": 100,
                                    "name": "",
                                    "node_type": 2,
                                    "depth": 3,
                                    "parent_id": 10,
                                    "is_hide": False,
                                    "related_id": "0",
                                    "related_group_map": 0,
                                    "children": [],
                                },
                                {
                                    "id": 101,
                                    "name": "1层",
                                    "node_type": 2,
                                    "depth": 3,
                                    "parent_id": 10,
                                    "is_hide": False,
                                    "related_id": "0",
                                    "related_group_map": 0,
                                    "children": [],
                                },
                            ],
                        },
                        {
                            "id": 200,
                            "name": "夜",
                            "node_type": 1,
                            "depth": 3,
                            "parent_id": 1,
                            "is_hide": True,
                            "related_id": "10",
                            "related_group_map": 201,
                            "children": [],
                        },
                        {
                            "id": 201,
                            "name": "昼",
                            "node_type": 1,
                            "depth": 3,
                            "parent_id": 1,
                            "is_hide": True,
                            "related_id": "0",
                            "related_group_map": 200,
                            "children": [],
                        },
                    ],
                }
            ]
        },
    }


def sample_point_list() -> dict:
    return {
        "retcode": 0,
        "data": {
            "point_list": [
                {"id": 5171, "label_id": 836, "x_pos": 1.0, "y_pos": 2.0, "related_jump_id": "901"},
                {"id": 5172, "label_id": 836, "x_pos": 3.0, "y_pos": 4.0, "related_jump_id": "0"},
                {"id": 5173, "label_id": 0, "x_pos": 5.0, "y_pos": 6.0, "related_jump_id": ""},
                {"id": 5174, "label_id": 836, "x_pos": 7.0, "y_pos": 8.0, "related_jump_id": None},
            ],
            "label_list": [],
        },
    }


def probe_of(outcomes: dict[str, str], source: str = "test") -> "callable":
    calls: list[str] = []

    def probe(map_id: str) -> MapProbeResult:
        calls.append(map_id)
        outcome = outcomes.get(str(map_id), PROBE_UNKNOWN)
        return MapProbeResult(str(map_id), outcome, (f"test probe: {map_id} → {outcome}",), source)

    probe.calls = calls  # type: ignore[attr-defined]
    return probe


# --------------------------------------------------------------------------- #
# 1) Tree extractor（§七 第一层）
# --------------------------------------------------------------------------- #


def test_tree_extractor_emits_edges_with_key_and_json_path():
    extraction = extract_tree(sample_tree())
    assert extraction.source == "map_tree"
    kinds = {}
    for edge in extraction.edges:
        kinds.setdefault(edge.edge_type, []).append(edge)
    assert sorted(kinds) == [MAP_GROUP, RELATED_MAP, TREE_CHILD]
    # 树：1→10、1→200、1→201、10→100、10→101 + related + group
    assert len(kinds[TREE_CHILD]) == 5
    assert {edge.source_map_id for edge in kinds[TREE_CHILD]} == {"1", "10"}
    assert len(kinds[RELATED_MAP]) == 1
    related = kinds[RELATED_MAP][0]
    assert (related.source_map_id, related.target_map_id) == ("200", "10")
    assert related.key == "related_id"
    assert related.json_path == "$.data.tree[0].children[1].related_id"
    assert related.raw_value == "10"
    assert related.discovery_source == "map_tree"
    # 每条边都能说清出处：键 + json 路径
    for edge in extraction.edges:
        assert edge.key and edge.json_path.startswith("$.data.tree")
        assert edge.provenance()["key"] == edge.key


def test_tree_extractor_filters_zero_blank_and_self_reference():
    payload = {
        "data": {
            "tree": [
                {"id": 1, "parent_id": 0, "related_id": "0", "related_group_map": 0, "children": []},
                {"id": 2, "parent_id": 1, "related_id": "", "related_group_map": "0", "children": []},
                {"id": 3, "parent_id": 1, "related_id": 3, "related_group_map": 3, "children": []},
            ]
        }
    }
    extraction = extract_tree(payload)
    assert [edge.edge_type for edge in extraction.edges] == [TREE_CHILD, TREE_CHILD]
    # 自环不许写成边（related_id / related_group_map 指向自己）
    assert all(edge.source_map_id != edge.target_map_id for edge in extraction.edges)
    # 树事实照样带出来（事实与边是两回事）
    facts = {fact.map_id: fact for fact in extraction.facts}
    assert facts["1"].is_hide is False
    assert facts["3"].name is None


def test_tree_extractor_records_tree_facts_for_m73():
    extraction = extract_tree(sample_tree())
    assert len(extraction.facts) == 6
    facts = {fact.map_id: fact for fact in extraction.facts}
    assert facts["100"].name_missing is True
    assert facts["101"].name == "1层"
    assert facts["200"].is_hide is True
    assert facts["100"].parent_id == "10"


# --------------------------------------------------------------------------- #
# 2) Map-info extractor（§七 第二层）
# --------------------------------------------------------------------------- #


def test_map_info_extractor_takes_real_name_from_children():
    payload = {
        "retcode": 0,
        "data": {
            "info": {
                "id": 10,
                "name": "海原市",
                "parent_id": 1,
                "node_type": 1,
                "depth": 2,
                "detail": None,
                "preview": "https://example.test/p.png",
                "related_id": "0",
                "related_group_map": 0,
                "children": [
                    {"id": 100, "name": "海原市-内部空间", "node_type": 2, "depth": 3},
                    {"id": 101, "name": "", "node_type": 2, "depth": 3},
                ],
            }
        },
    }
    extraction = extract_map_info(payload)
    facts = {fact.map_id: fact for fact in extraction.facts}
    # 深层地图的真名来自容器的 children[].name（M7.0 结论）
    assert facts["100"].name == "海原市-内部空间"
    assert facts["100"].json_path == "$.data.info.children[0].name"
    assert facts["101"].name_missing is True
    assert facts["10"].key == "info"
    assert facts["10"].preview == "https://example.test/p.png"
    assert facts["10"].has_detail is False
    # parent_id → TREE_CHILD（方向：父 → 自己），与 tree 的边同键
    assert [(edge.source_map_id, edge.target_map_id, edge.edge_type) for edge in extraction.edges] == [
        ("1", "10", TREE_CHILD)
    ]


def test_map_info_extractor_related_and_group_edges():
    payload = {
        "data": {
            "info": {
                "id": 200,
                "name": "夜",
                "parent_id": 1,
                "related_id": "10",
                "related_group_map": 201,
                "children": [],
            }
        }
    }
    extraction = extract_map_info(payload)
    by_type = {edge.edge_type: edge for edge in extraction.edges}
    assert by_type[RELATED_MAP].json_path == "$.data.info.related_id"
    assert by_type[MAP_GROUP].json_path == "$.data.info.related_group_map"
    assert by_type[RELATED_MAP].target_map_id == "10"
    assert by_type[MAP_GROUP].target_map_id == "201"


def test_map_info_extractor_without_info_is_a_diagnostic_not_a_crash():
    extraction = extract_map_info({"retcode": -1, "data": {}})
    assert extraction.edges == ()
    assert extraction.diagnostics


# --------------------------------------------------------------------------- #
# 3) Point-list extractor（§七 第三层）
# --------------------------------------------------------------------------- #


def test_point_list_extractor_turns_jump_into_edge_and_transition():
    extraction = extract_point_list(sample_point_list(), "100")
    assert extraction.counts() == {POINT_JUMP: 1}
    edge = extraction.edges[0]
    assert (edge.source_map_id, edge.target_map_id) == ("100", "901")
    assert edge.key == "related_jump_id"
    assert edge.json_path == "$.data.point_list[0].related_jump_id"
    assert edge.source_point_id == "5171"
    assert edge.source_label_id == "836"
    assert edge.discovery_source == "point_list"
    transition = extraction.transitions[0].as_transition()
    assert transition.point_id == 5171
    assert transition.target_map_source_id == "901"
    assert transition.transition_type == POINT_JUMP
    assert transition.action_label == ACTION_LABEL_REDIRECT
    assert transition.raw_json["json_path"].endswith("related_jump_id")
    # Viewer 只读 §十三 的形状
    assert transition.as_viewer()["target_map_id"] == "901"


def test_point_list_extractor_skips_zero_blank_and_none():
    """`0` / `"0"` / `""` / None 一律不是跳转：这是 M7.0 Q4 的规则。"""
    extraction = extract_point_list(sample_point_list(), "100")
    assert len(extraction.edges) == 1  # 5172("0") / 5173("") / 5174(None) 都不算
    assert len(extraction.transitions) == 1
    assert extraction.diagnostics == ()


def test_point_list_extractor_rejects_bad_source_map_id():
    extraction = extract_point_list(sample_point_list(), "not-an-id")
    assert extraction.edges == ()
    assert extraction.diagnostics


def test_point_list_extractor_records_unnormalizable_jump():
    payload = {"data": {"point_list": [{"id": 5, "label_id": 1, "related_jump_id": "979-1"}]}}
    extraction = extract_point_list(payload, "100")
    assert extraction.edges == ()
    assert any("979-1" in note for note in extraction.diagnostics)


# --------------------------------------------------------------------------- #
# 4) Point-info extractor（§七 第四层）
# --------------------------------------------------------------------------- #


def test_point_info_extractor_crosschecks_point_list():
    payload = {"data": {"info": {"id": 5171, "map_id": 100, "label_id": 836, "related_jump_id": "901"}}}
    extraction = extract_point_info(payload)
    assert extraction.counts() == {POINT_JUMP: 1}
    assert extraction.edges[0].discovery_source == "point_info"
    assert extraction.edges[0].json_path == "$.data.info.related_jump_id"
    assert extraction.transitions[0].target_map_source_id == "901"


def test_point_info_map_id_is_the_marker_layer_not_an_edge():
    """「跳转标点层」= point/info.map_id（bundle: pointMapId）——它不是地图边。"""
    payload = {"data": {"info": {"id": 5171, "map_id": 100, "related_jump_id": "0"}}}
    extraction = extract_point_info(payload)
    assert extraction.edges == ()
    assert any("related_jump_id" in note for note in extraction.diagnostics)


def test_point_info_without_map_id_does_not_guess_but_keeps_target():
    payload = {"data": {"info": {"id": 5171, "related_jump_id": "901"}}}
    extraction = extract_point_info(payload)
    assert extraction.edges == ()
    assert any("901" in note for note in extraction.diagnostics)


# --------------------------------------------------------------------------- #
# 5) Bundle contract extractor（§七 第五层）
# --------------------------------------------------------------------------- #

#: 真 bundle 的片段（sha256 e5cefc8f…，2026-10-01 抓的官方前端）：
#: 这里保留的是**决定语义的那几行**，不是整个 minified 文件。
BUNDLE_FIXTURE = (
    'if("0"!==e.related_jump_id){s.$analysis.trackEvent("click","","标点详情",t.id,o),'
    'u.default.prototype.$gemit("openRedirectConfirmation",{mapId:e.related_jump_id,pointId:e.id,order:e.order,extraInfo:o})}'
    "changeLayer:function(e){var t=this;r=t.currentMapId,a=t.pointMapId,o=t.mapId,"
    'l="plane"===e?a:o,"plane"===e?t.replaceRouteParams(l):(f.origin_map_id=t.pushOriginMapId(r))}'
    "t.resolveVisibleRelatedItem=function(e,t){var n=t,r=new Set,i=function(){if(r.has(n.id))return\"break\";"
    "r.add(n.id);var t=Number(n.related_id);n=e.find((function(e){return e.id===t}))};"
    'for(;n&&n.is_hide&&n.related_id&&"0"!==n.related_id;){if("break"===i())break}return n&&!n.is_hide?n:void 0}'
    "changeMapGroup:function(e){if(o=null===(r=t.currentGroup)||void 0===r?void 0:r.related_group_map){}}"
    "t.useOriginMapIds=function(){var t=(e.query||{}).origin_map_id;return t?String(t).split(\",\").filter(Boolean):[]}"
    "return{originMapIds:t,topOriginMapId:r,backOriginMapId:n,pushOriginMapId:function(e){}}"
    "t.MAP_GROUP_TYPE=Object.freeze({normal:0,light:1,night:2,foreign:3,beyond_the_tale:4})"
)


def test_bundle_contract_extractor_finds_navigation_symbols():
    extraction = extract_bundle_contract(BUNDLE_FIXTURE)
    hits = {hit.symbol: hit for hit in extraction.contracts}
    assert hits["openRedirectConfirmation"].found
    assert hits["openRedirectConfirmation"].implies_edge_type == POINT_JUMP
    assert hits["related_jump_id"].found
    assert hits["resolveVisibleRelatedItem"].implies_edge_type == RELATED_MAP
    assert hits["changeMapGroup"].implies_edge_type == MAP_GROUP
    assert hits["origin_map_id"].implies_edge_type == RETURN
    # 「跳转标点层」不是边
    assert hits["changeLayer"].found and hits["changeLayer"].implies_edge_type is None
    # bundle 里零出现的符号也要如实报（jump_target_id 不是 JUMP 来源）
    assert hits["jump_target_id"].found is False
    assert "jump_target_id" in extraction.diagnostics[0]


def test_bundle_contract_extractor_parses_map_group_type_enum():
    extraction = extract_bundle_contract(BUNDLE_FIXTURE)
    assert len(extraction.constants) == 1
    constant = extraction.constants[0]
    assert constant.name == "MAP_GROUP_TYPE"
    assert dict(constant.values) == {"normal": 0, "light": 1, "night": 2, "foreign": 3, "beyond_the_tale": 4}


def test_transition_registry_maps_keys_to_edge_types():
    assert contract_for_key("related_jump_id").edge_type == POINT_JUMP
    assert contract_for_key("related_id").edge_type == RELATED_MAP
    assert contract_for_key("related_group_map").edge_type == MAP_GROUP
    assert contract_for_key("origin_map_id").edge_type == RETURN
    # 已知不是边的键：语义有出处、但绝不产边
    for key in ("label_id", "jump_target_id", "jump_type", "map_group_type", "map_id", "game_map_id"):
        assert contract_for_key(key).edge_type is None
    assert contract_for_key("brand_new_map_id").space == SPACE_MAP  # 形态匹配，confidence < 1
    assert contract_for_key("brand_new_map_id").confidence < 1.0
    table = {item["key"]: item for item in bundle_key_contracts(extract_bundle_contract(BUNDLE_FIXTURE))}
    assert table["related_jump_id"]["bundle_found"] is True
    assert table["jump_target_id"]["bundle_found"] is False


# --------------------------------------------------------------------------- #
# ID Reference Scanner（§八）
# --------------------------------------------------------------------------- #


def test_absent_values_are_all_the_same_nothing():
    for value in (0, "0", "", "   ", None, False):
        assert is_absent(value) is True
    for value in (1, "1", "007", [1], "1,2"):
        assert is_absent(value) is False
    # bool / 空数组不是 id：当「无」处理，不许被当成 1 / 0
    assert is_absent(True) is True
    assert is_absent([]) is True
    assert as_map_id("007") == "7"
    assert as_map_id(1.0) == "1"
    assert as_map_id("abc") is None
    assert as_map_id(True) is None
    assert expand_id_values("1,2") == [("[0]", "1", "1"), ("[1]", "2", "2")]
    assert expand_id_values([0, "5", "", "x"]) == [("[1]", "5", "5")]
    assert expand_id_values("0") == []


def test_reference_key_shapes_follow_the_spec():
    for key in ("foo_id", "related_id", "related_group_map", "related_jump_id", "jump_target_id",
                "target_map", "group_id", "origin_map_id", "some_ids", "map_id"):
        assert is_reference_key(key) is True, key
    for key in ("id", "name", "node_type", "children", "x_pos", "map_shape", "detail"):
        assert is_reference_key(key) is False, key


def test_scanner_filters_zero_blank_and_counts_them():
    payload = {"a_id": 0, "b_id": "0", "c_id": "", "d_id": "   ", "e_id": None, "f_id": False}
    report = scan_id_references(payload)
    assert report.references == ()
    assert report.skipped_absent == 6
    assert report.by_conclusion() == {name: 0 for name in SCAN_CONCLUSIONS}


def test_scanner_walks_nested_paths_and_value_shapes():
    payload = {
        "data": {
            "list": [
                {"nested": {"target_map": "12"}},
                {"nested": {"some_ids": ["13", "0", 14]}},
            ]
        }
    }
    report = scan_id_references(payload, probe=probe_of({"12": PROBE_MAP_LIKE, "13": PROBE_MAP_LIKE,
                                                         "14": PROBE_MAP_LIKE}))
    paths = {item.json_path: item.map_id for item in report.references}
    assert paths == {
        "$.data.list[0].nested.target_map": "12",
        "$.data.list[1].nested.some_ids[0]": "13",
        "$.data.list[1].nested.some_ids[2]": "14",
    }
    assert report.skipped_absent == 1  # some_ids 里的 "0"


def test_scanner_without_probe_is_unresolved_not_a_guess():
    report = scan_id_references({"new_target_id": 1287})
    assert len(report.references) == 1
    item = report.references[0]
    assert item.conclusion == UNRESOLVED
    assert "不猜" in " ".join(item.reasons)


def test_scanner_must_probe_and_cannot_guess_from_id_overlap():
    """M7.0：187 个跳转目标里 180 个同时也是 label id —— 所以判定只能靠探测。

    同一个数字 979 既在 map 空间又在 label 空间：
    - `label_id` 的键语义是 label 引用 → NOT_A_MAP，但探测证据（RENDERABLE）照样记下来；
    - `foo_target_id` 语义未知、探测说是可渲染地图 → 候选跳转，**target 不许丢**。
    """
    probe = probe_of({"979": PROBE_RENDERABLE})
    payload = {
        "data": {
            "point": {
                "id": 5637,
                "label_id": 979,
                "related_jump_id": "979",
                "foo_target_id": 979,
                "new_jump_map": "979",
            }
        }
    }
    report = scan_id_references(payload, endpoint="point_list", probe=probe)
    by_key = {item.key: item for item in report.references}
    assert set(by_key) == {"label_id", "related_jump_id", "foo_target_id", "new_jump_map"}
    assert by_key["label_id"].conclusion == NOT_A_MAP
    assert by_key["label_id"].probe is not None and by_key["label_id"].probe.outcome == PROBE_RENDERABLE
    assert any("id 空间重叠" in reason for reason in by_key["label_id"].reasons)
    assert by_key["related_jump_id"].conclusion == UNKNOWN_RENDERABLE_TARGET
    assert by_key["related_jump_id"].claimed_by == "point_list"
    assert by_key["foo_target_id"].conclusion == UNKNOWN_RENDERABLE_TARGET
    assert by_key["foo_target_id"].claimed_by is None  # 未认领 → 最值得看的候选
    assert by_key["new_jump_map"].conclusion == UNKNOWN_RENDERABLE_TARGET
    # 每条结论都带六元组出处
    record = by_key["foo_target_id"].as_dict()
    for field_name in ("endpoint", "json_path", "key", "raw_value", "conclusion", "discovery_source"):
        assert field_name in record
    assert record["endpoint"] == "point_list"
    assert record["json_path"] == "$.data.point.foo_target_id"
    assert record["raw_value"] == 979
    # 探测真的被调用了（不是按区间猜的）
    assert probe.calls  # type: ignore[attr-defined]
    assert set(report.unclaimed_candidates()) == {by_key["foo_target_id"], by_key["new_jump_map"]}


def test_scanner_maps_probe_outcomes_to_conclusions():
    probe = probe_of({"1": PROBE_RENDERABLE, "2": PROBE_MAP_LIKE, "3": PROBE_NOT_MAP, "4": PROBE_UNKNOWN})
    report = scan_id_references(
        {"a_id": 1, "b_id": 2, "c_id": 3, "d_id": 4}, probe=probe
    )
    assert [item.conclusion for item in report.references] == [
        UNKNOWN_RENDERABLE_TARGET,
        MAP_LIKE_NON_RASTER,
        NOT_A_MAP,
        UNRESOLVED,
    ]
    assert report.by_conclusion() == {
        UNKNOWN_RENDERABLE_TARGET: 1,
        MAP_LIKE_NON_RASTER: 1,
        NOT_A_MAP: 1,
        UNRESOLVED: 1,
    }
    assert [item.as_dict()["map_id"] for item in report.candidates()] == ["1"]


def test_scanner_reports_non_id_values_as_diagnostics():
    report = scan_id_references({"weird_id": "979-1", "empty_ids": []})
    assert report.references == ()
    assert any("979-1" in note for note in report.diagnostics)


def test_scanner_key_contract_overrides_are_per_payload():
    """同名键在不同 payload 里可能是不同 id 空间：label/tree 的 parent_id 不是父地图。"""
    payload = {"data": {"tree": [{"id": 5, "parent_id": 979}]}}
    with_override = scan_id_references(payload, endpoint="label_tree", probe=probe_of({"979": PROBE_RENDERABLE}))
    without = scan_id_references(payload, endpoint="payload", probe=probe_of({"979": PROBE_RENDERABLE}))
    assert with_override.references[0].conclusion == NOT_A_MAP
    assert with_override.references[0].key_space == SPACE_LABEL
    assert without.references[0].conclusion == UNKNOWN_RENDERABLE_TARGET


def test_aggregate_scans_collects_conclusions_and_examples():
    probe = probe_of({"1": PROBE_RENDERABLE})
    reports = [
        scan_id_references({"a_id": 1, "b_id": 0}, endpoint="one", probe=probe),
        scan_id_references({"c_id": 1}, endpoint="two", probe=probe),
    ]
    summary = aggregate_scans(reports)
    assert summary["reports"] == 2
    assert summary["references"] == 2
    assert summary["skipped_absent"] == 1
    assert summary["by_conclusion"][UNKNOWN_RENDERABLE_TARGET] == 2
    assert set(summary["per_endpoint"]) == {"one", "two"}
    assert len(summary["unclaimed_candidates"]) == 2


# --------------------------------------------------------------------------- #
# 闭包收敛（§六，M7.1 的 closure 接口）
# --------------------------------------------------------------------------- #


def test_closure_converges_over_m72_extractors():
    tree_edges = [edge.as_edge() for edge in extract_tree(sample_tree()).edges]
    extractions = {
        "100": extract_point_list(sample_point_list(), "100"),
    }
    seen: list[str] = []

    def expand(map_id: str) -> list:
        seen.append(map_id)
        extraction = extractions.get(str(map_id))
        if extraction is None:
            #: 没有 payload ≠ 没有边：拿不到证据就返回空列表（有证据、没边）
            return []
        return [edge.as_edge() for edge in extraction.edges_from(map_id)]

    result = closure(tree_edges, seeds=["1"], expand=expand)
    assert result.converged is True
    assert result.frontier == ()
    assert result.unevidenced == ()
    assert "100" in result.visited and "901" in result.visited
    assert ("100", "901") in [(edge.source_map_id, edge.target_map_id) for edge in result.traversed]
    # 每个地图只展开一次（cycle 不会重复拉）
    assert len(seen) == len(set(seen))


def test_closure_keeps_cycles_but_does_not_loop():
    tree_edges = [edge.as_edge() for edge in extract_tree(sample_tree()).edges]
    jump = extract_point_list(sample_point_list(), "100").edges[0].as_edge()
    back = extract_point_list({"data": {"point_list": [{"id": 9, "related_jump_id": "1"}]}}, "901").edges[0].as_edge()
    result = closure([*tree_edges, jump, back], seeds=["1"])
    assert result.converged is True
    assert ("901", "1") in result.cycles  # A → B → A 是合法导航，不是 bug
    assert result.visited.count("1") == 1


def test_closure_without_evidence_is_not_converged():
    def expand(map_id: str):
        return None

    result = closure(seeds=["1"], expand=expand)
    assert result.unevidenced == ("1",)
    assert result.converged is False


@pytest.mark.parametrize("map_id", ["0", "", None])
def test_candidate_edge_requires_real_endpoints(map_id):
    from hsrmap.discovery import CandidateEdge

    with pytest.raises(ValueError):
        CandidateEdge(
            source_map_id=map_id,
            target_map_id="1",
            edge_type=TREE_CHILD,
            key="parent_id",
            json_path="$.x",
            discovery_source="map_tree",
        )
