from __future__ import annotations

import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Optional
import json

import requests
from requests import Response

from config import settings
from .uagents import random_user_agent

BLOCKED_STATUSES = {403, 429, 503}


class HttpClient:
    def __init__(self, proxy_url: Optional[str] = settings.proxy_url):
        self.session = requests.Session()
        self.proxy_url = proxy_url

    def _build_headers(self, *, headers: Optional[dict] = None, referer: Optional[str] = None) -> dict:
        h: dict[str, str] = {
            "User-Agent": random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
            "DNT": "1",
        }
        if referer:
            h["Referer"] = referer
        if headers:
            h.update(headers)
        return h

    def get(
        self,
        url: str,
        *,
        headers: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: int = 15,
        retries: int = 3,
        referer: Optional[str] = None,
        allow_statuses: Optional[set[int]] = None,
        use_proxy: bool = True,
    ) -> Response:
        proxies = {"http": self.proxy_url, "https": self.proxy_url} if (self.proxy_url and use_proxy) else None
        last_exc: Exception | None = None

        for attempt in range(1, retries + 1):
            time.sleep(random.uniform(0.5, 1.6))
            h = self._build_headers(headers=headers, referer=referer)
            try:
                resp = self.session.get(url, headers=h, params=params, proxies=proxies, timeout=timeout)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == retries:
                    raise
                time.sleep(min(6.0, (2 ** attempt) + random.random()))
                continue

            if resp.status_code == 200:
                return resp
            if allow_statuses and resp.status_code in allow_statuses:
                return resp
            if resp.status_code in BLOCKED_STATUSES and attempt < retries:
                time.sleep(min(8.0, (2 ** attempt) + random.random()))
                continue

            resp.raise_for_status()

        if last_exc:
            raise last_exc
        raise RuntimeError(f"HTTP request failed for {url}")


http_client = HttpClient()


def is_probably_blocked(html: str) -> bool:
    txt = html.lower()
    blocked_signals = [
        "access denied",
        "forbidden",
        "captcha",
        "verify you are human",
        "attention required",
        "cloudflare",
        "datadome",
        "cf-challenge",
    ]
    return any(s in txt for s in blocked_signals)


def fetch_html(url: str, *, referer: Optional[str] = None, timeout: int = 18) -> Optional[str]:
    resp = http_client.get(
        url,
        referer=referer,
        timeout=timeout,
        retries=3,
        allow_statuses=BLOCKED_STATUSES | {404},
    )
    if resp.status_code == 404:
        return None
    if resp.status_code in BLOCKED_STATUSES:
        html = get_with_selenium(url, timeout_sec=max(12, timeout))
        if html and not is_probably_blocked(html):
            return html
        return None
    if resp.status_code == 200 and resp.text and not is_probably_blocked(resp.text):
        return resp.text

    # Selenium fallback first when explicitly enabled, then Playwright fallback.
    if os.getenv("USE_SELENIUM_FALLBACK", "true").lower() in ("1", "true", "yes"):
        html = get_with_selenium(url, timeout_sec=max(12, timeout))
        if html and not is_probably_blocked(html):
            return html
    return get_with_playwright(url, timeout_ms=timeout * 1000)


def _selenium_sync_fetch(url: str, *, timeout_sec: int = 20) -> Optional[str]:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager
    except Exception:
        return None

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--user-agent={random_user_agent()}")
    options.add_argument("--lang=fr-FR")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = None
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(timeout_sec)
        driver.get(url)
        time.sleep(random.uniform(0.8, 1.6))
        html = driver.page_source
        if not html or is_probably_blocked(html):
            return None
        return html
    except Exception:
        return None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def get_with_selenium(url: str, *, timeout_sec: int = 20) -> Optional[str]:
    try:
        import asyncio

        asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_selenium_sync_fetch, url, timeout_sec=timeout_sec)
            try:
                return fut.result(timeout=timeout_sec + 10)
            except FuturesTimeoutError:
                return None
    except RuntimeError:
        return _selenium_sync_fetch(url, timeout_sec=timeout_sec)


def _playwright_sync_fetch(url: str, *, timeout_ms: int = 15000) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        try:
            context = browser.new_context(
                user_agent=random_user_agent(),
                locale="fr-FR",
                timezone_id="Europe/Paris",
                viewport={"width": 1366, "height": 768},
            )
            context.set_extra_http_headers(
                {
                    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
                }
            )
            page = context.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(random.randint(500, 1500))
            html = page.content()
            if is_probably_blocked(html):
                return None
            return html
        finally:
            browser.close()


def get_with_playwright(url: str, *, timeout_ms: int = 15000) -> Optional[str]:
    try:
        import asyncio

        asyncio.get_running_loop()
        # We are inside asyncio loop: run sync Playwright in a worker thread.
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_playwright_sync_fetch, url, timeout_ms=timeout_ms)
            try:
                return fut.result(timeout=(timeout_ms / 1000) + 5)
            except FuturesTimeoutError:
                return None
    except RuntimeError:
        # No running loop in this thread.
        return _playwright_sync_fetch(url, timeout_ms=timeout_ms)


def _playwright_sync_fetch_json(
    url: str,
    *,
    method: str = "POST",
    json_body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    warmup_url: str = "https://www.seloger.com/",
    timeout_ms: int = 30000,
) -> tuple[int, Optional[Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return (0, None)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        try:
            context = browser.new_context(
                user_agent=random_user_agent(),
                locale="fr-FR",
                timezone_id="Europe/Paris",
                viewport={"width": 1366, "height": 768},
            )
            page = context.new_page()
            page.goto(warmup_url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(random.randint(800, 1800))

            req_headers = {
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "origin": "https://www.seloger.com",
                "referer": "https://www.seloger.com/classified-search",
            }
            if headers:
                req_headers.update(headers)

            payload = json.dumps(json_body) if json_body is not None else None
            result = page.evaluate(
                """
                async ({ url, method, headers, payload }) => {
                    const res = await fetch(url, {
                        method,
                        headers,
                        body: payload,
                        credentials: 'include',
                    });
                    const txt = await res.text();
                    return { status: res.status, text: txt };
                }
                """,
                {
                    "url": url,
                    "method": method.upper(),
                    "headers": req_headers,
                    "payload": payload,
                },
            )
            status = int(result.get("status", 0))
            txt = result.get("text") or ""
            if status < 200 or status >= 300:
                return (status, None)
            try:
                return (status, json.loads(txt))
            except Exception:
                return (status, None)
        except Exception:
            return (0, None)
        finally:
            browser.close()


def fetch_json_with_playwright(
    url: str,
    *,
    method: str = "POST",
    json_body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    warmup_url: str = "https://www.seloger.com/",
    timeout_ms: int = 30000,
) -> Optional[Any]:
    try:
        import asyncio

        asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(
                _playwright_sync_fetch_json,
                url,
                method=method,
                json_body=json_body,
                headers=headers,
                warmup_url=warmup_url,
                timeout_ms=timeout_ms,
            )
            try:
                _, data = fut.result(timeout=(timeout_ms / 1000) + 8)
                return data
            except FuturesTimeoutError:
                return None
    except RuntimeError:
        _, data = _playwright_sync_fetch_json(
            url,
            method=method,
            json_body=json_body,
            headers=headers,
            warmup_url=warmup_url,
            timeout_ms=timeout_ms,
        )
        return data
