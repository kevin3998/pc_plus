"""
core/browser.py
─────────────────────────────────────────────────────────────
Patchright browser backend for search pages that require real JS rendering.
"""

import logging
import base64
import sys
import time
from pathlib import Path

from config.settings import ROOT_DIR
from core.cookie_manager import CookieManager

log = logging.getLogger("browser")


CHALLENGE_SIGNALS = [
    "are you a robot",
    "captcha challenge",
    "just a moment",
    "checking your browser",
    "enable javascript and cookies",
    "verify you are human",
]


class BrowserEngine:
    """Small Patchright wrapper with a persistent visible Chrome context."""

    def __init__(
        self,
        cookie_manager: CookieManager,
        profile_dir: Path | None = None,
        headless: bool = False,
        inject_cookies: bool = False,
    ):
        self.cm = cookie_manager
        self.profile_dir = profile_dir or (ROOT_DIR / "browser_profile")
        self.headless = headless
        self.inject_cookies = inject_cookies
        self._playwright = None
        self._context = None
        self._page = None
        self._started = False

    def start(self, domain: str = ""):
        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            print(
                "\n❌ 缺少 Patchright，请安装：\n"
                "   python -m pip install -r requirements.txt\n"
                "   patchright install chromium\n"
            )
            sys.exit(1)

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        log.info("正在启动 Patchright Chrome 浏览器...")
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel="chrome",
            headless=self.headless,
            no_viewport=True,
            accept_downloads=True,
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        if self.inject_cookies:
            self._inject_cookies(domain)
        self._started = True
        log.info("✓ Patchright 浏览器已就绪")

    def stop(self):
        if self._context:
            self._sync_cookies_out()
            self._context.close()
        if self._playwright:
            self._playwright.stop()
        self._started = False
        log.info("Patchright 浏览器已关闭")

    def goto(self, url: str, timeout: int = 60000) -> str:
        if not self._started:
            self.start()

        self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        self._wait_for_stable()
        if self._is_challenge_page():
            self._handle_challenge()
        self._sync_cookies_out()
        return self.html()

    def wait_for_user(self, message: str):
        print(message)
        try:
            input("[完成后按 Enter 继续] > ")
        except (KeyboardInterrupt, EOFError):
            print("\n用户中断")
            sys.exit(0)
        self._wait_for_stable()
        self._sync_cookies_out()

    def open_article(self, url: str, timeout: int = 60000) -> str:
        html = self.goto(url, timeout=timeout)
        self.scroll_to_bottom()
        return self.html() or html

    def html(self) -> str:
        return self._page.content()

    def current_url(self) -> str:
        return self._page.url

    def download_binary(self, url: str, referer: str = "", timeout: int = 30) -> dict:
        if not self._context:
            return {"status": 0, "content_type": "", "data": None, "error": "browser_not_started"}
        context_result = self._download_with_context_request(url, referer=referer, timeout=timeout)
        if not _should_try_page_download(url, context_result):
            return context_result
        download_result = self._download_with_page_event(url, timeout=timeout)
        if download_result.get("data"):
            return download_result
        return context_result

    def _download_with_context_request(self, url: str, referer: str = "", timeout: int = 30) -> dict:
        headers = {}
        if referer:
            headers["Referer"] = referer
        try:
            response = self._context.request.get(
                url,
                headers=headers,
                timeout=timeout * 1000,
            )
            headers_map = response.headers
            return {
                "status": response.status,
                "content_type": headers_map.get("content-type", ""),
                "data": response.body(),
            }
        except Exception as exc:
            return self._download_with_page_fetch(url, referer=referer)

    def _download_with_page_fetch(self, url: str, referer: str = "") -> dict:
        if not self._page:
            return {"status": 0, "content_type": "", "data": None, "error": "browser_not_started"}
        try:
            result = self._page.evaluate(
                """
                async ({url, referer}) => {
                  const headers = {};
                  if (referer) headers["Referer"] = referer;
                  const response = await fetch(url, {headers, credentials: "include"});
                  const buffer = await response.arrayBuffer();
                  let binary = "";
                  const bytes = new Uint8Array(buffer);
                  const chunk = 0x8000;
                  for (let i = 0; i < bytes.length; i += chunk) {
                    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                  }
                  return {
                    status: response.status,
                    content_type: response.headers.get("content-type") || "",
                    data_b64: btoa(binary),
                  };
                }
                """,
                {"url": url, "referer": referer},
            )
            data = base64.b64decode(result.get("data_b64", "")) if result.get("data_b64") else None
            return {
                "status": result.get("status", 0),
                "content_type": result.get("content_type", ""),
                "data": data,
            }
        except Exception as exc:
            return {"status": 0, "content_type": "", "data": None, "error": str(exc)}

    def _download_with_page_event(self, url: str, timeout: int = 30) -> dict:
        page = self._context.new_page()
        try:
            with page.expect_download(timeout=timeout * 1000) as download_info:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            download = download_info.value
            path = download.path()
            data = Path(path).read_bytes() if path else None
            return {
                "status": 200 if data else 0,
                "content_type": "application/pdf",
                "data": data,
            }
        except Exception as exc:
            return {"status": 0, "content_type": "", "data": None, "error": str(exc)}
        finally:
            try:
                page.close()
            except Exception:
                pass

    def scroll_to_bottom(self):
        if not self._page:
            return
        self._page.evaluate(
            """
            async () => {
              const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
              let last = 0;
              for (let i = 0; i < 12; i++) {
                window.scrollBy(0, 650);
                await delay(250);
                const now = window.scrollY;
                if (now === last) break;
                last = now;
              }
            }
            """
        )
        self._wait_for_stable(short=True)

    def click_next(self, selectors: list[str]) -> bool:
        for selector in selectors:
            locator = self._page.locator(selector).first
            try:
                if locator.count() == 0 or not locator.is_visible(timeout=1500):
                    continue
                locator.scroll_into_view_if_needed(timeout=3000)
                locator.click(timeout=5000)
                self._wait_for_stable()
                if self._is_challenge_page():
                    self._handle_challenge()
                self._sync_cookies_out()
                return True
            except Exception as e:
                log.debug(f"下一页点击失败 [{selector}]: {e}")
        return False

    def _wait_for_stable(self, short: bool = False):
        try:
            self._page.wait_for_load_state("networkidle", timeout=5000 if short else 12000)
        except Exception:
            pass
        time.sleep(0.5 if short else 1.0)

    def _is_challenge_page(self) -> bool:
        try:
            text = self._page.locator("body").inner_text(timeout=3000).lower()
            title = self._page.title().lower()
            return any(signal in text or signal in title for signal in CHALLENGE_SIGNALS)
        except Exception:
            return False

    def _handle_challenge(self):
        print(
            "\n检测到验证码/人机验证页面。"
            "请在弹出的 Chrome 窗口中完成验证，确认搜索结果页正常显示后回到终端按 Enter。"
        )
        try:
            input("[完成后按 Enter 继续] > ")
        except (KeyboardInterrupt, EOFError):
            print("\n用户中断")
            sys.exit(0)
        self._wait_for_stable()

    def _sync_cookies_out(self):
        if not self._context:
            return
        try:
            cookies = {c["name"]: c["value"] for c in self._context.cookies()}
            if cookies:
                self.cm.sync_from_session(cookies)
        except Exception as e:
            log.debug(f"浏览器 Cookie 同步失败: {e}")

    def _inject_cookies(self, domain: str):
        if not domain or not self.cm.cookies:
            return
        cookies = []
        cookie_domain = domain if domain.startswith(".") else f".{domain}"
        for name, value in self.cm.cookies.items():
            cookies.append({
                "name": name,
                "value": str(value),
                "domain": cookie_domain,
                "path": "/",
            })
        try:
            self._context.add_cookies(cookies)
            log.info(f"已注入 {len(cookies)} 个 Cookie 到 Patchright 上下文")
        except Exception as e:
            log.warning(f"Patchright Cookie 注入失败: {e}")


def _should_try_page_download(url: str, result: dict) -> bool:
    lower_url = url.lower()
    if "/pdf" not in lower_url and "/pdfft" not in lower_url:
        return False
    content_type = (result.get("content_type") or "").lower()
    data = result.get("data") or b""
    if result.get("status") != 200:
        return True
    if "application/pdf" in content_type or data.startswith(b"%PDF"):
        return False
    prefix = data[:2000].lstrip().lower()
    return "text/html" in content_type or prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html")
