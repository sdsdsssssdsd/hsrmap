from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hsrmap.guides.extract.blocks import is_ad_image

#: Source/page lifecycle (a1-6 §8). QA is the admission gate to Review.
PAGE_STATUSES = (
    "DISCOVERED",
    "FETCHED",
    "EXTRACTED",
    "QA_PASS",
    "QA_FAIL",
    "MATCHED",
    "EXHAUSTED",
    "REJECTED",
)

#: Typed QA failure reasons — never the bare "images_in_guide_assets=False".
QA_REASONS = (
    "NO_CONTENT",
    "NO_IMAGES",
    "ASSET_FETCH_FAILED",
    "JS_RENDER_REQUIRED",
    "HTTP_404",
    "LOW_INFORMATION",
    "PARSER_ERROR",
)

#: Only a page in this state (or with an explicit override) may produce Review items.
ADMISSION_STATUS = "QA_PASS"

#: Share of image blocks that must carry a stored asset. A single missing icon
#: on a 48-image page is not a broken source; a page whose images all failed is.
ASSET_COVERAGE_MIN = 0.9


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def inspect_import(result: dict[str, Any], store) -> dict[str, Any]:
    page = result["page"]
    blocks = result["blocks"]
    title = (page.get("title") or "").strip()
    author = (page.get("author") or "UNKNOWN").strip() or "UNKNOWN"
    images = [b for b in blocks if b.get("type") == "image"]
    ad_in_blocks = [b for b in images if is_ad_image(b.get("src") or "", b.get("alt") or "")]
    raw_html = Path(page["raw_html_path"]) if page.get("raw_html_path") else None
    stored = 0
    for image in images:
        sha = image.get("asset")
        if not sha:
            continue
        folder = store.assets_root / sha[:2]
        if folder.exists() and any(path.is_file() and path.name.startswith(sha) for path in folder.iterdir()):
            stored += 1
    coverage = (stored / len(images)) if images else 1.0
    assets_ok = coverage >= ASSET_COVERAGE_MIN
    dropped = int(getattr(blocks, "dropped_ads", 0) or 0)
    types = [b.get("type") for b in blocks]
    order_ok = "heading" in types and types.index("heading") <= max((i for i, t in enumerate(types) if t in {"paragraph", "image", "list"}), default=0)
    report = {
        "pass": bool(
            title
            and author
            and raw_html
            and raw_html.exists()
            and assets_ok
            and not ad_in_blocks
            and order_ok
        ),
        "title": title,
        "author": author,
        "published_at": page.get("published_at"),
        "blocks": blocks,
        "raw_html_exists": bool(raw_html and raw_html.exists()),
        "images_in_guide_assets": assets_ok,
        "asset_coverage": round(coverage, 4),
        "images_stored": stored,
        "ad_images_dropped": dropped,
        "empty_images_dropped": int(getattr(blocks, "dropped_empty_images", 0) or 0),
        "image_blocks": len(images),
        "order_ok": order_ok,
    }
    return report


def classify_failure(report: dict[str, Any]) -> str:
    """One typed reason for a QA failure, most fundamental cause first."""
    if not report.get("raw_html_exists"):
        return "PARSER_ERROR"
    blocks = report.get("blocks") or []
    images = int(report.get("image_blocks") or 0)
    if not blocks:
        return "JS_RENDER_REQUIRED"
    if images == 0:
        return "NO_IMAGES"
    if not report.get("images_in_guide_assets"):
        return "ASSET_FETCH_FAILED"
    if not (report.get("title") or "").strip() or (report.get("author") or "UNKNOWN") == "UNKNOWN":
        return "LOW_INFORMATION"
    if not report.get("order_ok"):
        return "PARSER_ERROR"
    return "LOW_INFORMATION"


def evaluate_import(report: dict[str, Any]) -> tuple[str, str | None]:
    """(qa_status, qa_reason) for one import report."""
    if report.get("pass"):
        return ADMISSION_STATUS, None
    return "QA_FAIL", classify_failure(report)


def record_qa(db, page_id: int, status: str, reason: str | None) -> None:
    db.conn.execute(
        "UPDATE guide_page SET qa_status = ?, qa_reason = ?, qa_checked_at = ? WHERE id = ?",
        (status, reason, _now(), int(page_id)),
    )
    db.conn.commit()


def set_override(db, page_id: int, *, operator: str, reason: str) -> None:
    """Manual QA_OVERRIDE: a named operator plus a reason, never a silent flag."""
    if not operator or not reason:
        raise ValueError("qa override requires operator and reason")
    db.conn.execute(
        """
        UPDATE guide_page
        SET qa_override_by = ?, qa_override_reason = ?, qa_override_at = ?
        WHERE id = ?
        """,
        (operator, reason, _now(), int(page_id)),
    )
    db.conn.commit()


def admits_review(page: dict[str, Any]) -> bool:
    """Does this page pass the Review admission gate?"""
    if page.get("qa_override_by") and page.get("qa_override_reason"):
        return True
    return str(page.get("qa_status") or "") == ADMISSION_STATUS
