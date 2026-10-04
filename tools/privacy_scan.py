"""Privacy scan for the packaged release (submit/)."""
import json
import os
import re
import sys
from pathlib import Path

TARGET = Path(sys.argv[1] if len(sys.argv) > 1 else "submit")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache", ".ruff_cache"}
#: 这个扫描器本身要写出它要找的模式（用户名、上层目录名），所以把它自己排除掉。
SELF_EXEMPT = {"tools/privacy_scan.py"}
BINARY = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".db", ".zip", ".woff", ".woff2", ".ttf", ".pdf", ".pyc"}

PATTERNS = [
    ("windows_path", re.compile(r"[A-Za-z]:[\\/](?![/\\])[^\s\"\x27<>|]{2,}")),
    ("unix_home", re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+")),
    ("local_username", re.compile(r"34054")),
    ("parent_project", re.compile(r"March7thAssistant")),
    ("api_key", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("gh_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("cookie_or_token", re.compile(r"(?i)(cookie|csrf|ltoken|ltuid|stoken|account_id)\s*[:=]\s*[\"\x27]?[A-Za-z0-9_\-]{8,}")),
]

env_key = ""
env_path = Path(".env")
if env_path.is_file():
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            env_key = line.split("=", 1)[1].strip()

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
                hits.append({
                    "file": rel,
                    "line": text[: match.start()].count("\n") + 1,
                    "kind": label,
                    "match": match.group(0)[:100],
                })
        if env_key and len(env_key) > 12 and env_key in text:
            hits.append({"file": rel, "line": 0, "kind": "deepseek_key", "match": "<key from .env>"})

print("target:", TARGET.resolve())
print("scanned:", files, "text files,", round(total_bytes / 1048576, 1), "MB")
print("hits:", len(hits))
by_kind = {}
by_file = {}
for hit in hits:
    by_kind[hit["kind"]] = by_kind.get(hit["kind"], 0) + 1
    by_file[hit["file"]] = by_file.get(hit["file"], 0) + 1
print("by kind:", json.dumps(by_kind, ensure_ascii=False))
for name, count in sorted(by_file.items(), key=lambda kv: -kv[1])[:30]:
    print("  %5d  %s" % (count, name))
Path("artifacts").mkdir(exist_ok=True)
Path("artifacts/privacy-hits.json").write_text(json.dumps(hits, ensure_ascii=False, indent=1), encoding="utf-8")
print("full list -> artifacts/privacy-hits.json")
