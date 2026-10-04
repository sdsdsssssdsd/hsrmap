from hsrmap.guides.vision.roles import classify_role


def test_waste_qr_is_unrelated_without_provider():
    out = classify_role(b"x", mime="image/webp", sha256="q", alt="17173APP", src="https://ue.example/17173wx.webp", provider=None)
    assert out["role"] == "unrelated"


def test_tiny_file_is_unrelated():
    assert classify_role(b"123", alt="海原市", src="x.png", provider=None)["role"] == "unrelated"


def test_gamersky_oss_promo_is_waste():
    src = "https://image.gamersky.com/webimg13/webgame_oss/c849d1c2-8c82-469c-b5ee-f25f987c18ea.jpg"
    assert classify_role(b"PNG-" + src.encode(), alt="", src=src, provider=None)["role"] in {"advertisement", "unrelated"}
    src = "https://imgs.gamersky.com/upimg/new_preview/2026/10/01/origin_b_202610010938215057.jpg"
    assert classify_role(b"PNG-" + src.encode(), alt="游民星空", src=src, provider=None)["role"] in {"advertisement", "unrelated"}


def test_image_groups_put_unlabeled_sidebar_in_waste():
    from hsrmap.guides.review.service import image_groups

    groups = image_groups(
        [
            {
                "sha": "ad",
                "url": "/guide-assets/ad",
                "src": "https://imgs.gamersky.com/upimg/new_preview/x.jpg",
                "alt": "育碧官宣",
                "role": "",
            },
            {
                "sha": "ok",
                "url": "/guide-assets/ok",
                "src": "https://img1.gamersky.com/image2024/02/haiyuan-1.png",
                "alt": "海原市",
                "role": "puzzle_step",
                "map_name": "海原市",
            },
        ]
    )
    assert "ad" in {img["sha"] for img in groups.get("废图") or []}
    assert "ok" in {img["sha"] for img in groups.get("海原市") or []}
    assert "ad" not in {img["sha"] for img in groups.get("海原市") or []}
    assert "ad" not in {img["sha"] for img in groups.get("未识别地区") or []}
