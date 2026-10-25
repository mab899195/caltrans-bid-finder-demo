"""HTTP access to Contractors Corner. Polite: one session, a pause between
requests, an identifying User-Agent, and no retries hammering the site."""
import time

import requests

import config


class Fetcher:
    def __init__(self, session=None, delay=config.REQUEST_DELAY_SECONDS):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json",
        })
        self.delay = delay
        self._last = 0.0

    def _get(self, url, **params):
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.session.get(url, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
        finally:
            self._last = time.monotonic()
        r.raise_for_status()
        return r

    def _get_page(self, **params):
        return self._get(config.PAGE_API, **params).json()

    def advertisements(self):
        return self._get_page(id=config.LIST_PAGE_ID, timespan="all")

    def upcoming(self):
        return self._get_page(id=config.LIST_PAGE_ID, timespan="upcoming")

    def project_details(self, contract):
        return self._get_page(id=config.DETAIL_PAGE_ID, ad_id=contract)

    def bid_summaries(self):
        return self._get_page(id=config.BID_SUMMARY_PAGE_ID)

    def attachment(self, sys_id):
        r = self._get(f"{config.ATTACHMENT_API}/{sys_id}")
        if "pdf" not in r.headers.get("Content-Type", ""):
            raise ValueError(f"expected a PDF, got {r.headers.get('Content-Type')}")
        return r.content
