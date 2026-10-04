"""攻略够不够：完成要求（两个维度）× 证据能力（三种）→ 六个状态。"""

import pytest

from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.stages import (
    COMPLETE_STATUS,
    LOCATE_AND_SOLVE,
    LOCATE_COMPLETE,
    LOCATE_MISSING,
    LOCATE_ONLY,
    NO_EVIDENCE,
    SCOPE_ONLY,
    SOLVE_INPUT_SEQUENCE,
    SOLVE_MISSING,
    SOLVE_NONE,
    completeness_report,
    evidence_state,
    has_solution_steps,
    is_done,
    point_requirement,
    point_status,
    topic_completion,
)


def test_a_topic_profile_says_what_finishing_a_point_takes():
    # 折纸小鸟：走到位置就能捡，位置证据就是完整攻略
    bird = topic_completion("origami_bird")
    assert bird["requirement"] == LOCATE_ONLY and bird["solve_kind"] == SOLVE_NONE
    # 浮脂溯源：到了之后才开始的 ROTATE 解谜
    grease = topic_completion("floating_grease")
    assert grease["requirement"] == LOCATE_AND_SOLVE
    assert grease["solve_kind"] == SOLVE_INPUT_SEQUENCE
    # 未知主题：保守地按「还要解」算，不假装它已经完成
    assert topic_completion("no_such_topic")["requirement"] == LOCATE_AND_SOLVE


def test_a_point_can_raise_its_own_requirement():
    # 官方说明写「位于…」：到点即得，主题默认照旧
    requirement, _kind, source = point_requirement("位于门旁的凳子上。", "nymph")
    assert (requirement, source) == (LOCATE_ONLY, "topic")
    # 同一个主题里写「完成此处…解谜获得。」的那个点：这个点还要解题
    requirement, _kind, source = point_requirement("完成此处「黄金替罪羊」解谜获得。", "origami_bird")
    assert (requirement, source) == (LOCATE_AND_SOLVE, "point")
    # 「按此处按钮…」是到了之后的交互
    requirement, kind, source = point_requirement("按此处按钮获得此处无名尘灵。", "origami_bird")
    assert (requirement, kind, source) == (LOCATE_AND_SOLVE, "INTERACT", "point")


def test_an_action_line_upgrades_the_point_and_counts_as_solve_evidence():
    """「击落空中气球获得。」是到了之后的动作，不是位置：要求上调，且算 SOLVE 证据。"""
    requirement, kind, source = point_requirement("击落空中气球获得。", "nameless_dust_spirit")
    assert (requirement, kind, source) == (LOCATE_AND_SOLVE, "INTERACT", "point")
    evidence = evidence_state(entry_key="5822", images=0, steps=["击落空中气球获得。"])
    assert evidence == {"scope": False, "locate": False, "solve": True}
    assert point_status(LOCATE_AND_SOLVE, evidence) == LOCATE_MISSING


def test_the_official_line_that_raised_the_point_is_its_own_solve_evidence():
    """官方说明自己写了怎么做时，同一句话既是上调理由、也是解法证据（若虫 2960/4114）。"""
    line = "位于电梯墙壁上。（电梯上升或下降时与其交互）"
    requirement, kind, source = point_requirement(line, "nymph")
    assert (requirement, kind, source) == (LOCATE_AND_SOLVE, "INTERACT", "point")
    # 主题默认「到点就行」+ 这一点被这行说明上调：调用方把这句话一并交给证据判断
    passed = evidence_state(
        entry_key="2960", images=1, steps=[line], official_point=True, official_text=line
    )
    assert passed["solve"] is True
    assert point_status(requirement, passed) == COMPLETE_STATUS
    # 主题默认就要解法的点位不传这句话：一行位置说明放不过真谜题
    withheld = evidence_state(
        entry_key="2960", images=1, steps=[line], official_point=True
    )
    assert withheld["solve"] is False
    assert point_status(requirement, withheld) == SOLVE_MISSING


def test_a_location_line_is_not_a_solution():
    assert not has_solution_steps([{"text": "位于门旁的凳子上。"}])
    assert has_solution_steps([{"text": "影子出现前：左-下-左-右-右-右-右。"}])
    assert has_solution_steps([{"text": "顺时针旋转两次，再逆时针一次。"}])
    assert has_solution_steps([{"text": "第一名若虫：点击雕像后面的按钮。"}])


def test_a_scope_line_is_not_location_evidence():
    """「此处地图区域存在1个王下一桶」说的是范围，不是位置——别把「地图」当定位字眼。"""
    scope = evidence_state(entry_key="1667", images=0, steps=["此处地图区域存在1个王下一桶"])
    assert scope["locate"] is False
    located = evidence_state(entry_key="5229", images=0, steps=["位于此处窗户上。"])
    assert located["locate"] is True


def test_three_evidence_capabilities():
    # 官方点位：一张官方截图 + 一行位置说明 → LOCATE 齐，没有解法
    official = evidence_state(entry_key="3481", images=1, steps=["位于门旁的凳子上。"])
    assert official == {"scope": False, "locate": True, "solve": False}
    # 范围页：「该地图共 N 个」——知道总数，不知道是哪一个
    scope = evidence_state(entry_key="set:nymph", images=0, steps=["该地图共 260 个若虫"])
    assert scope == {"scope": True, "locate": False, "solve": False}
    # 解法页：有方向序列，但没说是哪一个点
    solved = evidence_state(entry_key="3482", images=0, steps=["影子出现前：左-下-左-右-右-右-右。"])
    assert solved == {"scope": False, "locate": False, "solve": True}


@pytest.mark.parametrize(
    "requirement,evidence,expected",
    [
        (LOCATE_ONLY, {"scope": False, "locate": False, "solve": False}, NO_EVIDENCE),
        (LOCATE_ONLY, {"scope": True, "locate": False, "solve": False}, SCOPE_ONLY),
        (LOCATE_ONLY, {"scope": False, "locate": True, "solve": False}, LOCATE_COMPLETE),
        (LOCATE_AND_SOLVE, {"scope": True, "locate": False, "solve": False}, SCOPE_ONLY),
        (LOCATE_AND_SOLVE, {"scope": False, "locate": True, "solve": False}, SOLVE_MISSING),
        (LOCATE_AND_SOLVE, {"scope": False, "locate": False, "solve": True}, LOCATE_MISSING),
        (LOCATE_AND_SOLVE, {"scope": False, "locate": True, "solve": True}, COMPLETE_STATUS),
    ],
)
def test_the_status_machine_has_six_outcomes(requirement, evidence, expected):
    assert point_status(requirement, evidence) == expected


def test_done_means_a_reader_can_actually_get_the_point():
    # 「到点即得」的点位终点是 LOCATE_COMPLETE，「还要解」的点位终点是 COMPLETE
    assert is_done(LOCATE_COMPLETE) and is_done(COMPLETE_STATUS)
    assert not is_done(SOLVE_MISSING) and not is_done(NO_EVIDENCE)
    assert not is_done(SCOPE_ONLY) and not is_done(LOCATE_MISSING)


@pytest.mark.data  # 报告要真实官方点位（data/snapshots），submit 副本里没有
def test_the_report_scores_published_guides(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/a", "title": "t"})
    db.create_entry(
        {
            "source_point_id": "3481",
            "title": "官方点位",
            "status": "published",
            "page_id": page["id"],
            "steps": [{"text": "位于门旁的凳子上。", "images": ["sha-a"]}],
        }
    )
    db.conn.commit()
    report = completeness_report(db, topics=["nymph"], detail_db=tmp_path / "none.db")
    assert report["points"] >= 1
    row = next(item for item in report["rows"] if item["point"] == "3481")
    assert row["guide_id"] is not None
    # 若虫是「到点即得」：官方点位图 + 一行位置说明就够了
    assert row["status"] == LOCATE_COMPLETE and row["done"] is True
    assert report["statuses"][LOCATE_COMPLETE] >= 1
    assert report["done"] >= 1
    assert all(item["point"] != "3481" for item in report["missing_locate"])
    db.close()


@pytest.mark.data
def test_a_community_entry_with_the_solution_beats_the_newer_official_line(tmp_path):
    """官方补发的条目 id 更大，但它只有一行「完成此处…解谜获得。」。

    如果只看「最新的那条」，它会盖住旁边那条写着「Q为顺时针，E为逆时针」的社区攻略，
    点位就永远算缺解法（floating_grease 的 5016/5003/5000 就是这么被盖住的）。
    """
    db = GuideDatabase(tmp_path / "guide.db")
    db.create_entry(
        {
            "source_point_id": "5016",
            "title": "浮脂溯源·珠星大厦",
            "status": "published",
            "source_kind": "Community",
            "steps": [{"text": "进入机关后按 Q（顺时针）一次，再跳到右侧回收染料。"}],
        }
    )
    db.create_entry(
        {
            "source_point_id": "5016",
            "title": "浮脂溯源·官方点位",
            "status": "published",
            "source_kind": "Official",
            "steps": [{"text": "完成此处「浮脂溯源•二次元ROTATE！」解谜获得。"}],
        }
    )
    db.conn.commit()
    report = completeness_report(db, topics=["floating_grease"], detail_db=tmp_path / "none.db")
    row = next(item for item in report["rows"] if item["point"] == "5016")
    assert row["status"] == COMPLETE_STATUS and row["done"] is True
    assert row["source_kind"] == "Community"
    db.close()


@pytest.mark.data
def test_the_official_point_is_the_standard_for_phase_one(tmp_path):
    """用户 2026-10：第 1 阶段一切以官方为标准——官方地图上有这个点，就到得了。

    它还没有自己的条目（guide_id 为空），但定位不再算缺口；缺的只可能是 SOLVE。
    """
    db = GuideDatabase(tmp_path / "guide.db")
    report = completeness_report(db, topics=["nymph"], detail_db=tmp_path / "none.db")
    row = next(item for item in report["rows"] if item["point"] == "3481")
    assert row["guide_id"] is None
    assert row["status"] == LOCATE_COMPLETE and row["done"] is True
    db.close()


@pytest.mark.data  # 用真实官方点位（data/snapshots）；submit 副本里跳过
def test_a_neighbouring_point_id_does_not_count_as_a_guide(tmp_path):
    """388 不该被 3388 的攻略「覆盖」——以前用 LIKE '%388%' 就会这么算。"""
    db = GuideDatabase(tmp_path / "guide.db")
    db.create_entry(
        {
            "source_point_id": "3388",
            "title": "别的点位",
            "status": "published",
            "steps": [{"text": "位于某处。", "images": ["sha-a"]}],
        }
    )
    db.conn.commit()
    report = completeness_report(db, topics=["dimensional_trotter"], detail_db=tmp_path / "none.db")
    row = next(item for item in report["rows"] if item["point"] == "388")
    #: 关键是没有认领邻居的条目；定位本身由官方点位保证（第 1 阶段以官方为准）
    assert row["guide_id"] is None
    assert row["status"] == LOCATE_COMPLETE
    db.close()


@pytest.mark.data
def test_a_set_entry_that_names_the_point_counts_as_its_guide(tmp_path):
    """范围条目（set: 键）只要以完整 id 点名了这个点位，就算它的攻略。"""
    db = GuideDatabase(tmp_path / "guide.db")
    db.create_entry(
        {
            "source_point_id": "set:388-400:topic:dimensional_trotter",
            "title": "相位灵火 全收集",
            "status": "published",
            "steps": [{"text": "第 388 个在路旁。", "images": ["sha-a"]}],
        }
    )
    db.conn.commit()
    report = completeness_report(db, topics=["dimensional_trotter"], detail_db=tmp_path / "none.db")
    row = next(item for item in report["rows"] if item["point"] == "388")
    assert row["guide_id"] is not None and row["done"] is True
    db.close()
