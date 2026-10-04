"""结构指纹与漂移检测（a1-9 §18「API schema drift 可检测」）。

两条底线：
1. 指纹里**不能有值** —— 它会被记进仓库当基线，所以不许捎带 UID / 状态值；
2. 少字段和多字段都算漂移 —— 接口悄悄扩了字段同样要人看一眼。
"""

from __future__ import annotations

import json

from hsrmap.progress import shapes as shapes_mod

PAYLOAD = {
    "retcode": 0,
    "data": {
        "point_status": {"list": [{"point_id": "5171", "status": 2}, {"point_id": "5172", "status": 0}]},
        "uid": "812345679",
    },
}


def test_shape_records_fields_and_types_only() -> None:
    fields = shapes_mod.shape_of(PAYLOAD)
    assert "data.point_status.list[]" in fields
    assert not any("项" in field for field in fields), "数组长度是值不是形状，不许进指纹"
    assert "data.point_status.list[].point_id:str" in fields
    assert "data.point_status.list[].status:int" in fields
    joined = "\n".join(fields)
    assert "812345679" not in joined, "指纹里不许出现 UID"
    assert "5171" not in joined, "指纹里不许出现点位 id 这种值"


def test_fingerprint_is_stable_and_value_independent() -> None:
    same_shape_other_values = {
        "retcode": 0,
        "data": {"point_status": {"list": [{"point_id": "9999", "status": 1}]}, "uid": "1"},
    }
    assert shapes_mod.fingerprint(PAYLOAD) == shapes_mod.fingerprint(same_shape_other_values)
    assert shapes_mod.fingerprint(PAYLOAD) != shapes_mod.fingerprint({"retcode": 0, "data": {"uid": "1"}})


def test_compare_shape_flags_missing_and_added_fields() -> None:
    want = ["data.uid:str", "data.point_status:dict"]
    now = ["data.uid:str", "data.point_status:dict", "data.server:str"]
    diff = shapes_mod.compare_shape(want, now)
    assert diff["drift"] is True and diff["added"] == ["data.server:str"] and diff["missing"] == []
    dropped = shapes_mod.compare_shape(now, want)
    assert dropped["drift"] is True and dropped["missing"] == ["data.server:str"]
    assert shapes_mod.compare_shape(want, want)["drift"] is False


def test_baseline_roundtrip_and_drift_report(tmp_path) -> None:
    path = tmp_path / "shapes.json"
    shapes_mod.save_baseline(path, {"get_user_treasure_count": shapes_mod.shape_entry(PAYLOAD)})
    text = path.read_text(encoding="utf-8")
    assert "812345679" not in text and "5171" not in text
    baseline = shapes_mod.load_baseline(path)
    report = {"shapes": {"get_user_treasure_count": shapes_mod.shape_entry(PAYLOAD)}}
    clean = shapes_mod.drift_report(report, baseline=baseline)
    assert clean["drift"] is False and clean["baseline"] == 1
    #: 官方给同一个端点加了一个字段 → 必须报出来。
    grown = {"shapes": {"get_user_treasure_count": shapes_mod.shape_entry(
        {**PAYLOAD, "data": {**PAYLOAD["data"], "extra": 1}})}}
    drifted = shapes_mod.drift_report(grown, baseline=baseline)
    assert drifted["drift"] is True
    assert drifted["drifted"][0]["added_count"] >= 1


def test_unknown_endpoint_or_missing_report_counts_as_drift(tmp_path) -> None:
    baseline = {"get_point_status": {"fingerprint": "x", "fields": ["data:dict"]}}
    assert shapes_mod.drift_report({"shapes": {}}, baseline=baseline)["drift"] is True
    assert shapes_mod.drift_report({"shapes": {"brand_new": {"fingerprint": "y", "fields": []}}},
                                   baseline=baseline)["drift"] is True
    #: 没有基线时不算漂移，但要如实说「还没有基线」。
    empty = shapes_mod.drift_report({"shapes": {"a": {"fingerprint": "z", "fields": []}}}, baseline={})
    assert empty["drift"] is False and empty["note"]


def test_probe_report_carries_shapes_and_cli_reports_drift(tmp_path, capsys) -> None:
    from hsrmap.cli import main

    report = {
        "probe_version": 2,
        "shapes": {"binding_role_list": shapes_mod.shape_entry([{"game_uid": "1", "region": "prod_gf_cn"}])},
    }
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    baseline = tmp_path / "baseline.json"

    assert main(["progress", "drift", "--report", str(path), "--baseline", str(baseline)]) == 0
    assert main(["progress", "drift", "--report", str(path), "--baseline", str(baseline),
                 "--record"]) == 0
    assert json.loads(baseline.read_text(encoding="utf-8"))["endpoints"]
    assert main(["progress", "drift", "--report", str(path), "--baseline", str(baseline)]) == 0

    #: 端点少了一个字段 → rc=2（gate 拒绝）。
    changed = {"probe_version": 2, "shapes": {"binding_role_list": shapes_mod.shape_entry(
        [{"game_uid": "1"}])}}
    path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    assert main(["progress", "drift", "--report", str(path), "--baseline", str(baseline)]) == 2
    assert "漂移" in capsys.readouterr().out


def test_cli_drift_needs_a_report(tmp_path, capsys) -> None:
    from hsrmap.cli import main

    assert main(["progress", "drift", "--report", str(tmp_path / "nope.json")]) == 1
    assert "找不到探针报告" in capsys.readouterr().err
