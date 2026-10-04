"""RequestsController: the single HTTP path every integration uses.

Auth header injection, client-side throttling (sliding window + concurrency cap),
server-side rate-limit handling (Retry-After, cooldown, backoff), one forced re-auth on
401, transient 5xx/network retries, and ConnectorError mapping with secrets redacted.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable

import httpx

from api.integrations.models.data_models import RateLimitsInfo
from api.integrations.models.errors import AuthError, NotFoundError, RateLimitError, UpstreamError

log = logging.getLogger(__name__)

HeadersProvider = Callable[[], Awaitable[dict[str, str]]]
AuthFailureHook = Callable[[], Awaitable[None]]
Sleeper = Callable[[float], Awaitable[None]]

RATE_LIMIT_HEADER_PREFIXES = ("x-ratelimit", "retry-after", "x-rate-limit")


@dataclass
class RateLimitDecision:
    """Returned by a provider hook when a response must be treated as rate limited."""

    retry: bool  # True: wait and retry, False: give up now
    wait_seconds: float | None = None
    message: str = "rate limited"


ResponseHook = Callable[[httpx.Response], RateLimitDecision | None]


class RequestsController:
    def __init__(
        self,
        name: str,
        rate_limits: RateLimitsInfo,
        headers_provider: HeadersProvider,
        *,
        on_auth_failure: AuthFailureHook | None = None,
        response_hook: ResponseHook | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        sleeper: Sleeper | None = None,
        clock: Callable[[], float] = time.monotonic,
        transient_retries: int = 2,
    ) -> None:
        self.name = name
        self.limits = rate_limits
        self._headers_provider = headers_provider
        self._on_auth_failure = on_auth_failure
        self._response_hook = response_hook
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._sleep: Sleeper = sleeper or asyncio.sleep
        self._clock = clock
        self._transient_retries = transient_retries

        self._window: deque[float] = deque()
        self._window_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(1, rate_limits.concurrency))
        self._paused_until: float = 0.0  # set when upstream says "stop" (Retry-After / remaining=0)
        self._last_rate_limit_headers: dict[str, str] = {}


    async def make_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        async with self._semaphore:
            attempt = 0
            auth_retried = False
            transient_left = self._transient_retries
            while True:
                await self._throttle()
                req_headers = dict(await self._headers_provider())
                if headers:
                    req_headers.update(headers)
                try:
                    resp = await self._client.request(method, url, params=params, json=json, data=data, headers=req_headers)
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    if transient_left > 0:
                        transient_left -= 1
                        await self._sleep(self._backoff(attempt))
                        attempt += 1
                        continue
                    raise UpstreamError(f"{self.name}: network error: {exc.__class__.__name__}") from exc

                self._capture_rate_limit_headers(resp)

                decision = self._rate_limit_decision(resp)
                if decision is not None:
                    if not decision.retry:
                        raise RateLimitError(f"{self.name}: {decision.message}", status=resp.status_code,
                                             retry_after=decision.wait_seconds)
                    if attempt >= self.limits.max_retries:
                        raise RateLimitError(
                            f"{self.name}: rate limit still exceeded after {attempt} retr{'y' if attempt == 1 else 'ies'}"
                            f" ({attempt + 1} attempts)",
                            status=resp.status_code,
                            retry_after=decision.wait_seconds,
                        )
                    wait = decision.wait_seconds if decision.wait_seconds is not None else self._backoff(attempt)
                    wait = min(wait, self.limits.max_sleep)
                    log.warning("%s rate limited, sleeping %.1fs (attempt %d)", self.name, wait, attempt + 1)
                    self._paused_until = max(self._paused_until, self._clock() + wait)
                    await self._sleep(wait)
                    attempt += 1
                    continue

                if resp.status_code == 401 and self._on_auth_failure and not auth_retried:
                    auth_retried = True
                    await self._on_auth_failure()
                    continue

                if resp.status_code >= 500 and transient_left > 0:
                    transient_left -= 1
                    await self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue

                self._raise_for_status(resp)
                return resp

    def last_rate_limit_headers(self) -> dict[str, str]:
        return dict(self._last_rate_limit_headers)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


    async def _throttle(self) -> None:
        """Sliding-window limiter plus honouring any upstream pause."""
        while True:
            now = self._clock()
            if now < self._paused_until:
                await self._sleep(self._paused_until - now)
                continue
            limit = self.limits.calls_per_window
            if not limit:
                return
            async with self._window_lock:
                cutoff = now - self.limits.window_seconds
                while self._window and self._window[0] <= cutoff:
                    self._window.popleft()
                if len(self._window) < limit:
                    self._window.append(now)
                    return
                wait = self._window[0] + self.limits.window_seconds - now
            await self._sleep(max(wait, 0.05))

    def _backoff(self, attempt: int) -> float:
        base = self.limits.initial_sleep_period
        if self.limits.backoff_strategy == "linear":
            wait = base + attempt * self.limits.linear_backoff_rate
        else:
            wait = base * (self.limits.linear_backoff_rate ** attempt)
        jitter = random.uniform(0, 0.25 * wait)
        return min(wait + jitter, self.limits.max_sleep)

    def _rate_limit_decision(self, resp: httpx.Response) -> RateLimitDecision | None:
        if self._response_hook:
            decision = self._response_hook(resp)
            if decision is not None:
                return decision
        if resp.status_code in self.limits.rate_limit_codes:
            wait = self._retry_after(resp)
            return RateLimitDecision(retry=True, wait_seconds=wait, message=f"HTTP {resp.status_code}")
        return None

    def _retry_after(self, resp: httpx.Response) -> float | None:
        """Retry-After is either delta-seconds or an HTTP-date (RFC 7231). Fall back to the cooldown."""
        if not self.limits.honor_retry_after:
            return self.limits.cooldown_period
        raw = resp.headers.get("Retry-After")
        if raw:
            try:
                return max(0.0, min(float(raw), self.limits.max_sleep))
            except ValueError:
                try:
                    when = parsedate_to_datetime(raw)
                    delay = (when - datetime.now(timezone.utc)).total_seconds()
                    return max(0.0, min(delay, self.limits.max_sleep))
                except (TypeError, ValueError):
                    log.debug("%s: unparseable Retry-After header %r", self.name, raw)
        return self.limits.cooldown_period

    def _capture_rate_limit_headers(self, resp: httpx.Response) -> None:
        captured = {k: v for k, v in resp.headers.items() if k.lower().startswith(RATE_LIMIT_HEADER_PREFIXES)}
        if captured:
            self._last_rate_limit_headers = captured
        remaining = resp.headers.get("X-RateLimit-Remaining") or resp.headers.get("X-Rate-Limit-Remaining")
        if remaining is not None:
            try:
                if int(remaining) <= 0:
                    # Upstream window exhausted (on any status): pause proactively until it rolls over.
                    self._paused_until = max(self._paused_until, self._clock() + self.limits.window_seconds)
            except ValueError:
                pass

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        snippet = _redact(resp.text[:300])
        if resp.status_code in (401, 403):
            raise AuthError(f"{self.name}: authentication failed ({resp.status_code})", status=resp.status_code)
        if resp.status_code == 404:
            raise NotFoundError(f"{self.name}: resource not found", status=404)
        raise UpstreamError(f"{self.name}: HTTP {resp.status_code}: {snippet}", status=resp.status_code)


def _redact(text: str) -> str:
    """Strip anything that looks like a token or key from error snippets."""
    return re.sub(r"(?i)(api[_-]?key|token|secret|password)\"?\s*[:=]\s*\"?[^\s\",}]+", r"\1=***", text)
