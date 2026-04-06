from __future__ import annotations

import random
import time
from typing import Optional

import requests
from requests import Response

from config import settings
from .uagents import random_user_agent


class HttpClient:
    def __init__(self, proxy_url: Optional[str] = settings.proxy_url):
        self.session = requests.Session()
        self.proxy_url = proxy_url

    def get(self, url: str, *, headers: Optional[dict] = None, params: Optional[dict] = None, timeout: int = 15) -> Response:
        time.sleep(random.uniform(0.3, 1.2))
        h = {
            "User-Agent": random_user_agent(),
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if headers:
            h.update(headers)
        proxies = {"http": self.proxy_url, "https": self.proxy_url} if self.proxy_url else None
        resp = self.session.get(url, headers=h, params=params, proxies=proxies, timeout=timeout)
        resp.raise_for_status()
        return resp


http_client = HttpClient()


def get_with_playwright(url: str, *, timeout_ms: int = 15000) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=random_user_agent())
            page = context.new_page()
            page.goto(url, timeout=timeout_ms)
            html = page.content()
            return html
        finally:
            browser.close()