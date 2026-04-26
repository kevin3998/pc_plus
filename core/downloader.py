"""Small binary downloader used by the browser parsing pipeline."""

import logging

import requests

from config.settings import DEFAULT_USER_AGENT
from core.cookie_manager import CookieManager

log = logging.getLogger("downloader")


class BinaryDownloadSession:
    """Download figures/PDFs with the current cookie snapshot.

    Page HTML is fetched by Patchright. This class is intentionally limited to
    secondary binary assets so the old HTTP crawling path stays out of the CLI.
    """

    def __init__(self, cookie_manager: CookieManager):
        self.cookie_manager = cookie_manager

    def download_binary(self, url: str, referer: str = "") -> bytes | None:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
        }
        if referer:
            headers["Referer"] = referer

        try:
            response = requests.get(
                url,
                headers=headers,
                cookies=self.cookie_manager.cookies,
                timeout=30,
            )
            if response.status_code == 200 and response.content:
                return response.content
            log.debug("binary download failed: status=%s url=%s", response.status_code, url)
        except requests.RequestException as exc:
            log.debug("binary download error: %s url=%s", exc, url)
        return None
