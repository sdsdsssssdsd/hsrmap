"""Privacy scan for the packaged release (submit/).

三层判定，越往下越严格：

1. **模式层**：先认出「像凭据 / 像本机绝对路径 / 像邮箱」的字符串；
2. **形态层**：把明显不是「值」的东西排掉 —— 全大写的常量名不是 token；
   保留域名的邮箱不是个人信息；\`C:\\Users\\\` 这种光秃秃的根不是用户名；
   驱动器号后面又出现冒号的多半是正则片段，不是路径；
3. **复核层**：仍然命中的，只有在 \`tools/privacy_allowlist.json\` 里**逐条复核过**才放过；
   凭据类命中（cookie_or_token / api_key / gh_token / deepseek_key）**永远不许**进白名单 ——
   白名单是为了少一点噪音，不是为了把真凭据放行。

退出码：\`0\` = 没有未复核命中；\`2\` = 有未复核命中（发布前必须处理）。
"""
import json
import os
import re
import sys
from pathlib import Path

#: 用法：privacy_scan.py [目标目录] [--out 命中清单路径]
#: 默认目标 \`submit\`（发布包）；命中清单默认 \`artifacts/privacy-hits.json\`。
_argv = [item for item in sys.argv[1:] if not item.startswith("--")]
_flags = {item for item in sys.argv[1:] if item.startswith("--")}
TARGET = Path(_argv[0]) if _argv else Path("submit")
if "--out" in sys.argv:
    HITS_OUT = Path(sys.argv[sys.argv.index("--out") + 1])
else:
    HITS_OUT = Path("artifacts/privacy-hits.json")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache", ".ruff_cache"}
#: 这两个文件按定义就要写出「命中的原文」（扫描器写出模式、白名单写下被放行的文本），
#: 所以把它们自己排除掉；白名单里本来也不允许出现凭据类命中（见 CREDENTIAL_KINDS）。
SELF_EXEMPT = {"tools/privacy_scan.py", "tools/privacy_allowlist.json"}
BINARY = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".db", ".zip", ".woff", ".woff2", ".ttf", ".pdf", ".pyc"}

PATTERNS = [
    ("windows_path", re.compile(r"[A-Za-z]:[\\/](?![/\\])[^\s\"\'<>|]{2,}")),
    ("unix_home", re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+")),
    ("local_username", re.compile(r"34054")),
    ("parent_project", re.compile(r"March7thAssistant")),
    ("api_key", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("gh_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    #: 米游社/HoYoLAB 的凭据键带版本后缀（ltoken_v2 / ltuid_v2 / ltmid_v2 / cookie_token），
    #: 后缀必须显式允许，否则扫描器会漏掉真实泄漏（这条是被 tests/test_progress_boundary.py 抓出来的）。
    ("cookie_or_token", re.compile(
        r"(?i)(cookie|csrf|ltoken|ltuid|ltmid|stoken|cookie_token|account_id)(?:_v\d+)?\s*[:=]\s*[\"\']?[A-Za-z0-9_\-]{8,}"
    )),
]

#: 这些是「值」而不是「名字」的命中类别；白名单对它们无效。
CREDENTIAL_KINDS = frozenset({"cookie_or_token", "api_key", "gh_token", "deepseek_key"})

#: 公共安装路径 / 空用户名根：不含任何身份信息。
#: 注意匹配常常在空格处被截断（\`C:\\Program Files\\...\` 只匹配到 \`C:\\Program\`），
#: 所以前缀按"第一个路径段"写，而不是写全路径。
PUBLIC_PATHS = (
    "c:\\program",
    "c:\\windows",
    "c:\\users\\",
    "/applications/",
)
#: 保留给文档用的域名（RFC 2606 / RFC 6761）：出现它们不代表泄漏了谁的邮箱。
RESERVED_DOMAINS = ("example.com", "example.org", "example.net", "example.test", "localhost")
RESERVED_SUFFIXES = (".test", ".invalid", ".example", ".localhost")

ALLOWLIST_PATH = Path(__file__).with_name("privacy_allowlist.json")


def load_allowlist(path: Path = ALLOWLIST_PATH) -> dict[tuple[str, str, str], str]:
    """复核过的命中：(文件, 类别, 命中文本) -> 为什么可以放过。"""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, str, str], str] = {}
    for item in data.get("allow") or []:
        kind = str(item.get("kind") or "")
        if kind in CREDENTIAL_KINDS:
            raise SystemExit(f"privacy_allowlist.json 不许放行凭据类命中：{item!r}")
        out[(str(item.get("file") or ""), kind, str(item.get("match") or ""))] = str(item.get("why") or "")
    return out


def _value_part(match: str) -> str:
    for sep in ("=", ":"):
        if sep in match:
            return match.split(sep, 1)[1].strip().strip("\"\'")
    return ""


def _is_constant_name(match: str) -> bool:
    """\`ENV_COOKIE = "HSRMAP_HOYOLAB_COOKIE"\`：右边是全大写常量名，不是凭据值。"""
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{7,}", _value_part(match)))


def _is_public_path(match: str) -> bool:
    low = match.lower()
    if any(low.startswith(prefix) for prefix in PUBLIC_PATHS):
        #: \`C:\\Users\\\` 后面什么都没有：那是「别写用户名」这句话本身，不是某个人的目录。
        return True
    #: 驱动器号之后又冒出冒号（\`p:/global:\`）：那是正则/键名片段，不是路径。
    return ":" in match[2:]


def _is_reserved_email(match: str) -> bool:
    _, _, domain = match.partition("@")
    domain = domain.lower()
    if not domain:
        return False
    return domain in RESERVED_DOMAINS or domain.endswith(RESERVED_SUFFIXES)


def is_noise(kind: str, match: str) -> bool:
    """形态层：这条命中是不是「长得像但其实不是」。"""
    if kind == "cookie_or_token":
        return _is_constant_name(match)
    if kind == "windows_path":
        return _is_public_path(match)
    if kind == "email":
        return _is_reserved_email(match)
    return False


env_key = ""
env_path = Path(".env")
if env_path.is_file():
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            env_key = line.split("=", 1)[1].strip()

allowlist = load_allowlist()
hits = []
files = 0
total_bytes = 0
for base, dirs, names in os.walk(TARGET):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for name in names:
        path = Path(base) / name
        rel = path.relative_to(TARGET).as_posix()
        if rel in SELF_EXEMPT or path.suffix.lower() in BINARY:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files += 1
        total_bytes += len(text)
        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                snippet = match.group(0)[:100]
                if is_noise(label, snippet):
                    continue
                hits.append({
                    "file": rel,
                    "line": text[: match.start()].count("\n") + 1,
                    "kind": label,
                    "match": snippet,
                    "allowlisted": (rel, label, snippet) in allowlist,
                })
        if env_key and len(env_key) > 12 and env_key in text:
            hits.append({"file": rel, "line": 0, "kind": "deepseek_key", "match": "<key from .env>",
                         "allowlisted": False})

reviewed = [hit for hit in hits if hit["allowlisted"]]
unreviewed = [hit for hit in hits if not hit["allowlisted"]]
print("target:", TARGET.resolve())
print("scanned:", files, "text files,", round(total_bytes / 1048576, 1), "MB")
print("hits:", len(hits), "(reviewed-allowlisted:", len(reviewed), "· unreviewed:", len(unreviewed), ")")
by_kind = {}
by_file = {}
for hit in unreviewed:
    by_kind[hit["kind"]] = by_kind.get(hit["kind"], 0) + 1
    by_file[hit["file"]] = by_file.get(hit["file"], 0) + 1
print("by kind:", json.dumps(by_kind, ensure_ascii=False))
for name, count in sorted(by_file.items(), key=lambda kv: -kv[1])[:30]:
    print("  %5d  %s" % (count, name))
for hit in reviewed[:10]:
    print("  allowlisted: %s:%s %s" % (hit["file"], hit["line"], hit["kind"]))
try:
    HITS_OUT.parent.mkdir(parents=True, exist_ok=True)
    HITS_OUT.write_text(json.dumps(hits, ensure_ascii=False, indent=1), encoding="utf-8")
    print("full list ->", HITS_OUT)
except OSError as exc:  # noqa: BLE001 - 写不出清单不该改变「扫到了什么」这个结论
    print("warn: 命中清单写不出去（不影响上面的判定）：", exc)
if unreviewed:
    print("RESULT: FAIL —— 有未复核命中，发布前必须处理（或写进 tools/privacy_allowlist.json 并说明理由）")
    raise SystemExit(2)
print("RESULT: OK —— 没有未复核命中")
