"""只读请求器（a1-9 §9）：`request(method, host, path, params, json_body, headers, credential)`。

四件事只在这一层做，别处不要重复实现：

* **只读**：每个请求都要先过 `endpoints.read_contract()`，写端点直接抛异常，根本到不了网络层；
* **凭据只进头部**：cookie 只在 `Cookie` 头里出现一次；异常、日志、debug dump 全部走脱敏出口
  （`cookie.mask_mapping` / `assert_no_secret`）；
* **成功条件是 `retcode == 0`**：HTTP 200 不等于接口成功，两者分开报；
* **可注入 transport**：测试与探针可以用假客户端跑完整流程，不碰网络。

没有 cookie 时它照样能被构造，只是带 `auth=cookie` 的端点会明确报「缺凭据」——
「没有 cookie」是合法状态，不是崩溃理由。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from hsrmap.progress.cookie import Credential, assert_no_secret, mask_mapping
from hsrmap.progress.endpoints import EndpointContract, read_contract
from hsrmap.progress.realm import SrMapRealm

USER_AGENT = "hsrmap-progress/6 (read-only; +https://github.com/sdsdsssssdsd/hsrmap)"
#: 地图 App 自己会带的公共参数（bundle 里的 defaultFormatParams）。
APP_SN = "sr_map"
DEFAULT_LANG = "zh-cn"


class ProgressError(RuntimeError):
    """progress 层的基类异常：只带 endpoint / 状态码 / retcode，绝不带凭据。"""


class CredentialRequired(ProgressError):
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        super().__init__(f"{endpoint} 需要 cookie：设置 HSRMAP_HOYOLAB_COOKIE 后重试")


class ProgressTransportError(ProgressError):
    pass


class ProgressApiError(ProgressError):
    def __init__(self, endpoint: str, retcode: Any, message: str, status: int) -> None:
        self.endpoint = endpoint
        self.retcode = retcode
        self.status = status
        super().__init__(f"{endpoint} 失败：http={status} retcode={retcode} message={message[:120]}")


@dataclass
class ProgressResponse:
    endpoint: str
    url: str
    status: int
    retcode: int | None
    message: str
    data: Any = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.retcode == 0


class ReadOnlyClient:
    """按合同发只读请求；所有出口都是脱敏的。"""

    def __init__(
        self,
        realm: SrMapRealm,
        *,
        credential: Credential | None = None,
        contracts: Mapping[str, EndpointContract] | None = None,
        app_version: str | None = None,
        transport: Callable[[str, str, Mapping[str, Any] | None, Mapping[str, Any] | None, Mapping[str, str]], tuple[int, str]] | None = None,
        min_interval: float = 0.5,
        timeout: float = 20.0,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if contracts is None:
            from hsrmap.progress.endpoints import load_contracts

            contracts = load_contracts()
        self.realm = realm
        self.credential = credential
        self.contracts = dict(contracts)
        self.app_version = app_version
        self.min_interval = float(min_interval)
        self.timeout = float(timeout)
        self.retries = int(retries)
        self._sleep = sleep
        self._transport = transport
        self._last = 0.0
        self.exchanges: list[dict[str, Any]] = []

    # -- helpers ---------------------------------------------------------
    def _throttle(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            self._sleep(self.min_interval - gap)
        self._last = time.monotonic()

    def _common_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"app_sn": APP_SN, "lang": DEFAULT_LANG}
        if self.app_version:
            params["app_version"] = self.app_version
        return params

    def _host_for(self, contract: EndpointContract, item: Mapping[str, Any]) -> str:
        #: binding 与 srmap 是两个 family：端点自己声明 host 时用它，否则用 realm 的 map host。
        if str(item.get("host") or "") == "binding":
            return self.realm.binding_host
        return self.realm.map_host

    def _send(
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None,
        json_body: Mapping[str, Any] | None,
        headers: Mapping[str, str],
    ) -> tuple[int, str]:
        if self._transport is not None:
            return self._transport(method, url, params, json_body, headers)
        import httpx

        last: Exception | None = None
        for attempt in range(max(1, self.retries + 1)):
            try:
                response = httpx.request(
                    method, url, params=dict(params or {}), json=dict(json_body or {}) if json_body else None,
                    headers=dict(headers), timeout=self.timeout, follow_redirects=False,
                )
                return response.status_code, response.text
            except Exception as exc:  # noqa: BLE001 - 网络异常统一成 transport error（信息里不含凭据）
                last = exc
                if attempt < self.retries:
                    self._sleep(1.0 * (attempt + 1))
        raise ProgressTransportError(f"{url} 请求失败：{type(last).__name__}")

    # -- public ----------------------------------------------------------
    def request(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        require_cookie: bool = True,
    ) -> ProgressResponse:
        registry_item = self._registry_item(endpoint)
        contract = read_contract(self.contracts, endpoint)  # 写端点在这里就被拒
        if require_cookie and contract.needs_cookie and self.credential is None:
            raise CredentialRequired(endpoint)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if contract.needs_cookie and self.credential is not None:
            headers["Cookie"] = self.credential.header()
        #: 公共参数只发给 srmap 一族：binding API 是另一个 family，别塞它不认识的东西。
        common = self._common_params() if contract.name != "binding_role_list" else {}
        merged = {**common, **(params or {})}
        if contract.method == "GET":
            body = None
        else:
            body = {**common, **(json_body or {})}
        url = self._host_for(contract, registry_item) + contract.path
        self._throttle()
        status, text = self._send(contract.method, url, merged if contract.method == "GET" else None, body, headers)
        response = self._parse(endpoint, url, status, text)
        self._record(contract, merged, body, status, response)
        return response

    def _registry_item(self, endpoint: str) -> dict[str, Any]:
        from hsrmap.progress.realm import load_registry

        return dict((load_registry().get("endpoints") or {}).get(endpoint) or {})

    def _parse(self, endpoint: str, url: str, status: int, text: str) -> ProgressResponse:
        payload: dict[str, Any] = {}
        try:
            import json

            loaded = json.loads(text) if text else {}
            payload = loaded if isinstance(loaded, dict) else {"data": loaded}
        except Exception:  # noqa: BLE001 - 非 JSON 响应照样要报出来（但不带凭据）
            payload = {"message": text[:200]}
        retcode = payload.get("retcode")
        return ProgressResponse(
            endpoint=endpoint,
            url=url,
            status=int(status),
            retcode=None if retcode is None else int(retcode),
            message=str(payload.get("message") or payload.get("msg") or ""),
            data=payload.get("data"),
            payload=payload,
        )

    def _record(
        self,
        contract: EndpointContract,
        params: Mapping[str, Any] | None,
        body: Mapping[str, Any] | None,
        status: int,
        response: ProgressResponse,
    ) -> None:
        """debug 出口：请求/响应都脱敏，并且自检一次（有凭据就直接炸，而不是写日志）。"""
        entry = {
            "endpoint": contract.name,
            "method": contract.method,
            "path": contract.path,
            "params": mask_mapping(params or {}),
            "body": mask_mapping(body or {}) if body else None,
            "status": status,
            "retcode": response.retcode,
            "message": response.message[:200],
            "cookie_sent": bool(self.credential is not None and contract.needs_cookie),
        }
        if self.credential is not None:
            assert_no_secret(repr(entry), self.credential.header())
        self.exchanges.append(entry)

    def require_ok(self, response: ProgressResponse) -> ProgressResponse:
        """需要「接口真的成功」时用这个，把 retcode != 0 变成异常。"""
        if not response.ok:
            raise ProgressApiError(response.endpoint, response.retcode, response.message, response.status)
        return response
