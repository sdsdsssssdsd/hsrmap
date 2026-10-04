"""Published audit: image-only steps are content, invented steps are not."""

import json

from hsrmap.cli import main
from hsrmap.guide_db import GuideDatabase
from hsrmap.guides.audit import audit_entries, empty_step_rows, fix_empty_steps


def _page(db, url, text):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": url, "title": "标题", "author": "作者"})
    path = db.path.parent / f"page-{page['id']}.txt"
    path.write_text(text, encoding="utf-8")
    db.conn.execute("UPDATE guide_page SET raw_text_path = ? WHERE id = ?", (str(path), page["id"]))
    db.conn.commit()
    return page


def _guide(db, url, steps, images=()):
    return db.create_entry(
        {
            "source_point_id": "1",
            "title": "攻略",
            "status": "published",
            "source_url": url,
            "steps": [{"text": text, "images": list(images) if index == 0 else []} for index, text in enumerate(steps)],
        }
    )


def _entries(db):
    from hsrmap.guides.publishing.diff import entry_snapshot

    return entry_snapshot(db)


def _problems(report):
    return {item["problem"] for finding in report["findings"] for item in finding["problems"]}


def test_supported_step_passes_normalization(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(db, "https://t.test/a", "第一步：转！\n第二步 向右")
    _guide(db, page["canonical_url"], ["第一步:转!", "第二步向右"])
    report = audit_entries(_entries(db), db)
    assert report["guides_with_problems"] == 0
    assert report["counts"]["HALLUCINATED_STEP"] == 0
    db.close()


def test_invented_step_is_reported_as_hallucination(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(db, "https://t.test/b", "第一步 转")
    _guide(db, page["canonical_url"], ["第一步 转", "凭空捏造的第三步"])
    report = audit_entries(_entries(db), db)
    assert report["counts"]["HALLUCINATED_STEP"] == 1
    assert len(report["hallucinated"]) == 1
    assert report["hallucinated"][0]["problems"][0]["detail"].startswith("凭空")
    db.close()


def test_image_only_step_is_content_not_noise(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(db, "https://t.test/c", "看图")
    _guide(db, page["canonical_url"], [""], images=["a" * 64])
    report = audit_entries(_entries(db), db, assets_root=tmp_path / "assets")
    problems = _problems(report)
    assert "IMAGE_ONLY_STEP" in problems
    assert "EMPTY_STEP_NO_ASSET" not in problems
    assert report["hallucinated"] == []
    db.close()


def test_step_without_text_or_image_is_flagged(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(db, "https://t.test/d", "x")
    _guide(db, page["canonical_url"], [""])
    report = audit_entries(_entries(db), db)
    assert "EMPTY_STEP_NO_ASSET" in _problems(report)
    db.close()


def test_missing_source_page_and_asset_are_reported(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    _guide(db, "https://t.test/missing", ["第一步"], images=["b" * 64])
    report = audit_entries(_entries(db), db, assets_root=tmp_path / "empty-assets")
    problems = _problems(report)
    assert "SOURCE_PAGE_MISSING" in problems
    assert "MISSING_ASSET" in problems
    db.close()


def test_fix_empty_steps_keeps_image_only_rows(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(db, "https://t.test/e", "x")
    guide = _guide(db, page["canonical_url"], ["", ""], images=["c" * 64])
    rows = empty_step_rows(db)
    assert len(rows) == 2
    assert {row["assets"] for row in rows} == {1, 0}
    dry = fix_empty_steps(db, apply=False)
    assert dry["image_only"] == 1 and dry["removable"] == 1
    assert len(empty_step_rows(db)) == 2
    applied = fix_empty_steps(db, apply=True)
    assert applied["removed"] and len(empty_step_rows(db)) == 1
    left = db.conn.execute("SELECT COUNT(*) AS c FROM guide_steps WHERE guide_id = ?", (guide["id"],)).fetchone()
    assert left["c"] == 1
    db.close()


def test_snapshot_diff_treats_hallucinated_steps_as_hard_fail(tmp_path):
    from hsrmap.guides.publishing.diff import blocking_reasons, snapshot_diff

    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    page = _page(working, "https://t.test/f", "第一步 转")
    _guide(working, page["canonical_url"], ["第一步 转", "捏造步骤"])
    report = snapshot_diff(
        working,
        published,
        assets_root=tmp_path / "assets",
        official_points={"test_topic": [{"source_point_id": "1", "map_id": "10"}]},
        topics=["test_topic"],
    )
    assert report["audit"]["counts"]["HALLUCINATED_STEP"] == 1
    assert len(report["audit"]["new_hallucinations"]) == 1
    assert any("hallucinated step" in reason for reason in report["hard_errors"])
    assert any("hallucinated step" in reason for reason in blocking_reasons(report, force=True))
    working.close()
    published.close()


def test_preexisting_hallucination_is_reported_and_now_blocks_too(tmp_path):
    """a1-8 六 起，捏造步骤只要在候选快照里就 HARD FAIL，不再区分新旧。

    旧规则只拦「这次新引入的」，旧债留着走 closure；规格改成一律拦，
    所以这里同时验证：新旧仍然分开报，但两条路都通不过 gate。
    """
    from hsrmap.guides.publishing.diff import blocking_reasons, snapshot_diff
    from hsrmap.guides.publishing.sync import sync_published

    working = GuideDatabase(tmp_path / "working.db")
    published = GuideDatabase(tmp_path / "published.db")
    page = _page(working, "https://t.test/h", "第一步 转")
    _guide(working, page["canonical_url"], ["第一步 转", "捏造步骤"])
    sync_published(working, published)
    report = snapshot_diff(
        working,
        published,
        assets_root=tmp_path / "assets",
        official_points={"test_topic": [{"source_point_id": "1", "map_id": "10"}]},
        topics=["test_topic"],
    )
    assert report["audit"]["counts"]["HALLUCINATED_STEP"] == 1
    assert report["audit"]["new_hallucinations"] == []
    assert report["audit"]["pre_existing_hallucinations"] == 1
    reasons = blocking_reasons(report)
    assert any("hallucinated step" in reason for reason in reasons)
    working.close()
    published.close()


def test_cli_published_audit_writes_json(tmp_path, capsys):
    working_path = tmp_path / "guide.db"
    published_path = tmp_path / "published.db"
    working = GuideDatabase(working_path)
    published = GuideDatabase(published_path)
    page = _page(working, "https://t.test/g", "第一步 转")
    guide = _guide(working, page["canonical_url"], ["第一步 转"])
    published.close()
    working.close()
    out = tmp_path / "audit.json"
    code = main([
        "guides", "published-audit",
        "--db", str(working_path),
        "--published", str(published_path),
        "--assets", str(tmp_path / "assets"),
        "--json", str(out),
    ])
    assert code == 0
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["guides_checked"] == 0  # the published DB is empty
    assert "empty_steps" in body
    assert guide["id"]

def test_interior_ordinal_step_is_grounded(tmp_path):
    """The article's own "第3个【梦境迷钟】" must ground the same step.  "第N个"
    is stripped from the step, so it has to be stripped from the page too."""
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(
        db,
        "https://t.test/ordinal",
        "第3个【梦境迷钟】在【房间2】里面，进入【房间2】后，从【酒塔1】获得次数后，从【桥梁1】上去",
    )
    _guide(db, page["canonical_url"], ["（1）第3个【梦境迷钟】在【房间2】里面，进入【房间2】后，从【酒塔1】获得次数后，从【桥梁1】上去"])
    report = audit_entries(_entries(db), db)
    assert report["counts"]["HALLUCINATED_STEP"] == 0
    db.close()


def test_assembled_label_is_derived_not_hallucinated(tmp_path):
    """A label built from words the page contains is debt, not invention."""
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(
        db,
        "https://t.test/assembled",
        "2层区域点位16王下一桶位置在旁边的墙壁上该王下一桶只能通过战斗获取3层区域点位20宝箱位置在惊梦酒吧传送点前方",
    )
    _guide(db, page["canonical_url"], ["3层区域 王下一桶"])
    report = audit_entries(_entries(db), db)
    assert report["counts"]["HALLUCINATED_STEP"] == 0
    assert report["counts"]["DERIVED_LABEL"] == 1
    assert report["hallucinated"] == []
    db.close()


def test_prose_never_passes_as_an_assembled_label(tmp_path):
    """The assembled-label tier is for short labels; long prose must be quoted."""
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(
        db,
        "https://t.test/prose",
        "第一处甲甲甲甲就在附近，第二处乙乙乙乙需要飞行，第三处丙丙丙丙在楼下，"
        "第四处丁丁丁丁在楼上，第五处戊戊戊戊在门外，第六处己己己己在屋顶，第七处庚庚庚庚在井底",
    )
    _guide(db, page["canonical_url"], ["甲甲甲甲 乙乙乙乙 丙丙丙丙 丁丁丁丁 戊戊戊戊 己己己己 庚庚庚庚"])
    report = audit_entries(_entries(db), db)
    assert report["counts"]["HALLUCINATED_STEP"] == 1
    db.close()


def _official_guide(db, *, text, point="3481", source_url="https://act.hoyolab.com/x.png"):
    return db.create_entry(
        {
            "source_point_id": point,
            "title": "若虫·官方点位",
            "status": "published",
            "source_kind": "Official",
            "source_url": source_url,
            "steps": [{"text": text}],
        }
    )


def _fake_details(monkeypatch, mapping):
    import hsrmap.guides.official as official

    monkeypatch.setattr(official, "latest_detail_db", lambda *a, **k: "detail.db")
    monkeypatch.setattr(
        official, "official_details", lambda db, ids: {pid: mapping[pid] for pid in ids if pid in mapping}
    )


def test_official_entry_is_grounded_by_the_official_point_line(tmp_path, monkeypatch):
    """官方点位条目没有来源网页，但它在官方地图里有出处：那一行点位说明。"""
    db = GuideDatabase(tmp_path / "guide.db")
    _official_guide(db, text="位于门旁的凳子上。")
    _fake_details(monkeypatch, {"3481": {"text": "位于门旁的凳子上。"}})
    report = audit_entries(_entries(db), db)
    assert report["guides_with_problems"] == 0
    assert report["counts"]["SOURCE_PAGE_MISSING"] == 0
    assert report["counts"]["UNGROUNDED_STEP"] == 0
    db.close()


def test_official_entry_with_no_detail_text_is_a_derived_label(tmp_path, monkeypatch):
    """官方说明是空的（例如二次元 JUMP）：步骤只可能是我们按地图名拼的标签。"""
    db = GuideDatabase(tmp_path / "guide.db")
    _official_guide(db, text="官方地图点位：2层")
    _fake_details(monkeypatch, {"3481": {"text": ""}})
    report = audit_entries(_entries(db), db)
    assert report["counts"]["DERIVED_LABEL"] == 1
    assert report["counts"]["UNGROUNDED_STEP"] == 0
    assert report["counts"]["SOURCE_PAGE_MISSING"] == 0
    db.close()


def test_official_step_that_is_not_in_the_official_line_is_a_hallucination(tmp_path, monkeypatch):
    db = GuideDatabase(tmp_path / "guide.db")
    _official_guide(db, text="凭空捏造的位置")
    _fake_details(monkeypatch, {"3481": {"text": "位于门旁的凳子上。"}})
    report = audit_entries(_entries(db), db)
    assert report["counts"]["HALLUCINATED_STEP"] == 1
    db.close()

def test_a_step_transcribed_from_the_entrys_own_image_is_grounded(tmp_path):
    """图解法转录：文字来自这条攻略自己带的图，写明是哪张图就不算幻觉。"""
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(db, "https://t.test/pic", "正文只有一句：解密步骤见下图")
    db.create_entry(
        {
            "source_point_id": "5016",
            "title": "浮脂溯源·二维市1号点位",
            "status": "published",
            "source_url": page["canonical_url"],
            "steps": [
                {"text": "[图解法转录 0087f6d9] 二维市1号点位：第1步 开局Q一下；第2步 向右跳回收染料。",
                 "images": ["0087f6d9" + "a" * 56]},
            ],
        }
    )
    db.conn.commit()
    report = audit_entries(_entries(db), db, assets_root=tmp_path / "assets")
    assert report["counts"]["HALLUCINATED_STEP"] == 0
    assert report["counts"]["IMAGE_TRANSCRIBED_STEP"] == 1
    assert report["hallucinated"] == []
    db.close()


def test_a_transcription_that_names_an_absent_image_is_still_a_hallucination(tmp_path):
    db = GuideDatabase(tmp_path / "guide.db")
    page = _page(db, "https://t.test/pic2", "正文只有一句：解密步骤见下图")
    db.create_entry(
        {
            "source_point_id": "5016",
            "title": "浮脂溯源·二维市1号点位",
            "status": "published",
            "source_url": page["canonical_url"],
            "steps": [{"text": "[图解法转录 deadbeef] 二维市1号点位：第1步 开局Q一下。", "images": ["cafe" + "b" * 60]}],
        }
    )
    db.conn.commit()
    report = audit_entries(_entries(db), db, assets_root=tmp_path / "assets")
    assert report["counts"]["HALLUCINATED_STEP"] == 1
    db.close()


def test_chrome_steps_are_pruned_and_steps_renumbered(tmp_path):
    """Sidebar text that became a step is removed, and the rest closes ranks."""
    from hsrmap.guides.audit import chrome_step_rows, prune_chrome_steps

    db = GuideDatabase(tmp_path / "guide.db")
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": "https://t.test/chrome", "title": "标题", "author": "作者"})
    html_path = tmp_path / "page.html"
    html_path.write_text(
        "<html><body><ul><li>本周5339人已下载</li></ul><p>正文第一段</p></body></html>",
        encoding="utf-8",
    )
    text_path = tmp_path / "page.txt"
    text_path.write_text("正文第一段", encoding="utf-8")
    db.conn.execute(
        "UPDATE guide_page SET raw_html_path = ?, raw_text_path = ? WHERE id = ?",
        (str(html_path), str(text_path), page["id"]),
    )
    db.conn.commit()
    guide = db.create_entry({
        "source_point_id": "1",
        "title": "攻略",
        "status": "published",
        "source_url": page["canonical_url"],
        "steps": [
            {"text": "本周5339人已下载", "images": ["b" * 64]},
            {"text": "正文第一段", "images": ["a" * 64]},
            {"text": "第二步 转", "images": []},
        ],
    })

    rows = chrome_step_rows(db)
    assert [row["text"] for row in rows] == ["本周5339人已下载"]
    dry = prune_chrome_steps(db, apply=False)
    assert dry["steps"] == 1 and dry["guides"] == 1
    left = db.conn.execute("SELECT COUNT(*) AS c FROM guide_steps WHERE guide_id = ?", (guide["id"],)).fetchone()
    assert left["c"] == 3

    applied = prune_chrome_steps(db, apply=True)
    assert applied["steps"] == 1
    steps = [
        dict(row)
        for row in db.conn.execute(
            "SELECT step_index, text FROM guide_steps WHERE guide_id = ? ORDER BY step_index",
            (guide["id"],),
        )
    ]
    assert [step["text"] for step in steps] == ["正文第一段", "第二步 转"]
    assert [step["step_index"] for step in steps] == [0, 1]
    assets = [
        dict(row)
        for row in db.conn.execute(
            "SELECT step_index, asset_sha256 FROM guide_assets WHERE guide_id = ? ORDER BY step_index",
            (guide["id"],),
        )
    ]
    # the image follows its step; the one on the chrome step is dropped
    assert [(asset["step_index"], asset["asset_sha256"]) for asset in assets] == [(0, "a" * 64)]
    assert prune_chrome_steps(db, apply=False)["steps"] == 0
    db.close()

def _chrome_only_page(db, tmp_path, url):
    source = db.upsert_source({"name": "t", "domain": "t.test"})
    page = db.add_page(source["id"], {"canonical_url": url, "title": "标题", "author": "作者"})
    html_path = tmp_path / f"p{page['id']}.html"
    html_path.write_text(
        "<html><body><ul><li>本周5776人已下载</li></ul><p>真正的正文</p></body></html>", encoding="utf-8"
    )
    text_path = tmp_path / f"p{page['id']}.txt"
    text_path.write_text("真正的正文", encoding="utf-8")
    db.conn.execute(
        "UPDATE guide_page SET raw_html_path = ?, raw_text_path = ? WHERE id = ?",
        (str(html_path), str(text_path), page["id"]),
    )
    db.conn.commit()
    return page


def test_prune_quarantines_a_guide_that_loses_every_step(tmp_path):
    """An entry with nothing left but chrome is not published empty."""
    from hsrmap.guides.audit import QUARANTINED_STATUS, empty_guide_ids, prune_chrome_steps

    db = GuideDatabase(tmp_path / "guide.db")
    page = _chrome_only_page(db, tmp_path, "https://t.test/chrome-only")
    guide = _guide(db, page["canonical_url"], ["本周5776人已下载"])
    assert empty_guide_ids(db) == []

    dry = prune_chrome_steps(db, apply=False, quarantine_empty=True)
    assert dry["steps"] == 1 and dry["quarantined"] == []
    assert db.conn.execute("SELECT COUNT(*) AS c FROM guide_steps").fetchone()["c"] == 1

    applied = prune_chrome_steps(db, apply=True, quarantine_empty=True)
    assert applied["steps"] == 1
    assert applied["quarantined"] == [guide["id"]]
    row = db.conn.execute("SELECT status FROM guide_entry WHERE id = ?", (guide["id"],)).fetchone()
    assert row["status"] == QUARANTINED_STATUS
    assert empty_guide_ids(db) == []  # nothing publishable is left empty
    assert prune_chrome_steps(db, apply=True, quarantine_empty=True)["steps"] == 0
    db.close()



