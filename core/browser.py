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
from urllib.parse import urlparse

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

ARTICLE_WAIT_SECONDS = 30
ARTICLE_WAIT_POLL_SECONDS = 2


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
            try:
                self._sync_cookies_out()
                self._context.close()
            except Exception as exc:
                log.debug("Patchright context close skipped: %s", exc)
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception as exc:
                log.debug("Patchright stop skipped: %s", exc)
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
        self._wait_for_article_content(url)
        return self.html() or html

    def html(self) -> str:
        return self._page.content()

    def current_url(self) -> str:
        return self._page.url

    def download_binary(self, url: str, referer: str = "", timeout: int = 30) -> dict:
        if not self._context:
            return {"status": 0, "content_type": "", "data": None, "error": "browser_not_started"}
        return self._download_with_context_request(url, referer=referer, timeout=timeout)

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

    def _wait_for_article_content(self, url: str):
        if not self._page or "sciencedirect.com" not in urlparse(url).netloc:
            self.scroll_to_bottom()
            return

        deadline = time.time() + ARTICLE_WAIT_SECONDS
        last_status = {}
        while time.time() < deadline:
            self._scroll_article_page()
            last_status = self._article_content_status()
            if last_status.get("has_body") or last_status.get("no_access"):
                break
            time.sleep(ARTICLE_WAIT_POLL_SECONDS)

        if last_status.get("has_body"):
            log.info("ScienceDirect 正文已加载: chars=%s headings=%s", last_status.get("chars"), last_status.get("headings"))
        elif last_status.get("no_access"):
            log.info("ScienceDirect 页面显示可能无全文权限")
        else:
            log.warning(
                "ScienceDirect 正文等待超时，继续交给解析层判定: chars=%s headings=%s",
                last_status.get("chars"),
                last_status.get("headings"),
            )
        self._sync_cookies_out()

    def _scroll_article_page(self):
        self._page.evaluate(
            """
            async () => {
              const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
              const steps = [0.25, 0.55, 0.85, 1.0];
              for (const step of steps) {
                window.scrollTo(0, Math.max(0, document.body.scrollHeight * step - window.innerHeight));
                await delay(500);
              }
              const article = document.querySelector("article") || document.body;
              if (article) article.scrollIntoView({block: "start"});
              await delay(300);
            }
            """
        )
        self._wait_for_stable(short=True)

    def _article_content_status(self) -> dict:
        try:
            return self._page.evaluate(
                """
                () => {
                  const text = (document.body && document.body.innerText || "").replace(/\\s+/g, " ");
                  const headings = Array.from(document.querySelectorAll("article h2, article h3, article h4"))
                    .map(el => (el.innerText || "").replace(/\\s+/g, " ").trim())
                    .filter(Boolean);
                  const ignored = new Set([
                    "highlights", "abstract", "graphical abstract", "keywords",
                    "cited by", "recommended articles", "references"
                  ]);
                  const contentHeadings = headings.filter(h => {
                    const lower = h.toLowerCase();
                    return !ignored.has(lower) &&
                      !lower.startsWith("cited by") &&
                      !lower.startsWith("recommended articles") &&
                      !lower.startsWith("references") &&
                      !/access through your organization|check access to the full text|sign in to access|get access|purchase pdf/i.test(lower);
                  });
                  const hasBodySelector = !!document.querySelector(
                    "div.Body, div#body, div[class*='article-body'], div[class*='ArticleBody'], section[class*='body']"
                  );
                  const hasIntro = headings.some(h => /(^|\\b)(\\d+\\.?\\s*)?introduction\\b/i.test(h));
                  const hasSection = contentHeadings.some(h =>
                    /\\b(experimental|methods?|results?|discussion|conclusion)\\b/i.test(h) ||
                    /\\bmaterials?\\s+and\\s+methods?\\b/i.test(h)
                  );
                  const noAccess = /access through your organization|check access to the full text|sign in to access|get access|purchase pdf/i.test(text);
                  return {
                    chars: text.length,
                    headings: contentHeadings.length,
                    has_body: hasBodySelector || hasIntro || hasSection || text.length > 12000,
                    no_access: noAccess && !hasBodySelector && !hasIntro && !hasSection,
                  };
                }
                """
            )
        except Exception as exc:
            log.debug("正文加载状态检测失败: %s", exc)
            return {}

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
