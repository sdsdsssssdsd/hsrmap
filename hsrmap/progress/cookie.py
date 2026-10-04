"""凭据边界（a1-9 §6/§7）：cookie 是不透明凭据，只从环境变量来，永不外泄。

规则（写进类型而不是写进注释）：

* 只有一个入口：`HSRMAP_HOYOLAB_COOKIE`；**不接受命令行参数**（会进 shell history / 进程表 /
  CI 日志），也不接受项目目录下的 cookie 文件；
* `Credential` 的 `repr` / `str` / `format` 全部脱敏，把它塞进异常、日志、JSON 都只得到 `<redacted>`；
* 不解析 cookie 的字段结构：`ltoken_v2`/`ltuid_v2` 现在够用，不代表以后够用，
  程序只负责「有没有 → 发请求 → 看 retcode → 给诊断」。缺哪个字段不是我们要猜的事；
* `redact()` 给日志与异常兜底；`assert_no_secret()` 给测试与 debug 出口兜底。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

#: 唯一允许的凭据入口。
ENV_COOKIE = "HSRMAP_HOYOLAB_COOKIE"

#: 出现这些词的地方，值必须被替换掉（兜底用）。
_SENSITIVE_KEYS = ("cookie", "ltoken", "ltuid", "ltmid", "stoken", "csrf", "authorization")


class CredentialUnavailable(RuntimeError):
    """环境里没有 cookie：调用方应当降级到「纯本地进度」，而不是崩。"""


@dataclass(frozen=True)
class Credential:
    """一份不透明凭据。值只在 `header()` 里出现，其它出口全是脱敏的。"""

    _value: str

    def __post_init__(self) -> None:
        if not str(self._value or "").strip():
            raise CredentialUnavailable(f"{ENV_COOKIE} 是空的")

    @property
    def present(self) -> bool:
        return bool(self._value)

    def header(self) -> str:
        """给 HTTP 头用的原值——**唯一**允许返回原值的方法。"""
        return self._value

    def __repr__(self) -> str:
        return "Credential(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def __format__(self, spec: str) -> str:
        return format("<redacted>", spec)

    def __reduce__(self):  # pickle 也不许把它序列化出去
        raise TypeError("Credential 不允许被序列化")


def credential_from_env(env: Mapping[str, str] | None = None) -> Credential | None:
    """有就返回凭据，没有返回 None（不是异常：没有 cookie 是合法状态）。"""
    source = os.environ if env is None else env
    raw = str(source.get(ENV_COOKIE) or "").strip()
    return Credential(raw) if raw else None


def require_credential(env: Mapping[str, str] | None = None) -> Credential:
    """需要凭据的命令用这个：没有就明确报错，并告诉用户怎么给。"""
    credential = credential_from_env(env)
    if credential is None:
        raise CredentialUnavailable(
            f"没有找到 {ENV_COOKIE}。做法：在**你自己的终端**里 set {ENV_COOKIE}=<cookie> 后重跑"
            "（不要写进命令行参数、不要放进项目目录、不要贴给别人）。"
        )
    return credential


def mask_uid(uid: Any) -> str:
    """UID 打码：留后 4 位，前面一律 `*`。报告里只允许出现这个形态。"""
    text = str(uid or "").strip()
    if not text:
        return ""
    tail = text[-4:]
    return "*" * max(0, len(text) - len(tail)) + tail


def redact(value: str, *secrets: str) -> str:
    """把凭据从任意字符串里抹掉（日志、异常、debug dump 的兜底）。"""
    text = str(value)
    for secret in secrets:
        if secret and len(str(secret)) >= 8:
            text = text.replace(str(secret), "<redacted>")
    return text


def mask_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    """打印请求/响应时用它：键名可疑的一律只留长度。"""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = str(key).lower()
        if any(word in lowered for word in _SENSITIVE_KEYS):
            out[str(key)] = f"<redacted:{len(str(value))}>"
        else:
            out[str(key)] = value
    return out


def assert_no_secret(text: str, *secrets: str) -> None:
    """debug 出口的自检：一旦发现凭据就抛错，而不是把日志写出去。"""
    for secret in secrets:
        if secret and len(str(secret)) >= 8 and str(secret) in str(text):
            raise AssertionError("凭据出现在不该出现的地方（a1-9 §6 的 never 规则）")
