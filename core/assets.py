"""Asset candidate discovery and binary download validation."""

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class AssetCandidate:
    type: str
    url: str
    source: str = ""
    label: str = ""
    caption: str = ""
    priority: int = 100
    content_type_hint: str = ""


@dataclass
class AssetDownloadResult:
    status: str
    url: str
    data: bytes | None = None
    content_type: str = ""
    error: str = ""
    size_bytes: int = 0
    method: str = "requests"


class AssetDownloader:
    def __init__(
        self,
        session,
        browser=None,
        browser_fallback: bool = True,
        timeout: int = 30,
        min_image_bytes: int = 1000,
    ):
        self.session = session
        self.browser = browser
        self.browser_fallback = browser_fallback
        self.timeout = timeout
        self.min_image_bytes = min_image_bytes

    def download_one(self, candidate: AssetCandidate, referer: str = "") -> AssetDownloadResult:
        request_result = self._download_with_session(candidate, referer)
        if request_result.status == "done":
            return request_result
        if self.browser_fallback and self.browser:
            browser_result = self._download_with_browser(candidate, referer)
            if browser_result.status == "done":
                return browser_result
            return browser_result
        return request_result

    def _download_with_session(self, candidate: AssetCandidate, referer: str) -> AssetDownloadResult:
        try:
            data = self.session.download_binary(candidate.url, referer=referer, timeout=self.timeout)
        except TypeError:
            data = self.session.download_binary(candidate.url, referer=referer)
        except Exception as exc:
            return AssetDownloadResult("failed", candidate.url, error=f"download_error:{exc}", method="requests")
        return self._coerce_result(candidate, data, method="requests")

    def _download_with_browser(self, candidate: AssetCandidate, referer: str) -> AssetDownloadResult:
        try:
            raw = self.browser.download_binary(candidate.url, referer=referer, timeout=self.timeout)
        except TypeError:
            raw = self.browser.download_binary(candidate.url, referer=referer)
        except Exception as exc:
            return AssetDownloadResult("failed", candidate.url, error=f"browser_error:{exc}", method="browser")
        return self._coerce_result(candidate, raw, method="browser")

    def _coerce_result(self, candidate: AssetCandidate, raw, method: str) -> AssetDownloadResult:
        if isinstance(raw, AssetDownloadResult):
            raw.method = raw.method or method
            return self._validate(candidate, raw)
        if isinstance(raw, dict):
            status = raw.get("status", 200)
            data = raw.get("data")
            content_type = raw.get("content_type", "")
            if status and int(status) != 200:
                return AssetDownloadResult("failed", candidate.url, data=data, content_type=content_type,
                                           error=f"http_{status}", size_bytes=len(data or b""), method=method)
            return self._validate(candidate, AssetDownloadResult(
                "done" if data else "failed",
                candidate.url,
                data=data,
                content_type=content_type,
                error="" if data else "empty",
                size_bytes=len(data or b""),
                method=method,
            ))
        if raw:
            return self._validate(candidate, AssetDownloadResult(
                "done",
                candidate.url,
                data=raw,
                content_type=candidate.content_type_hint,
                size_bytes=len(raw),
                method=method,
            ))
        return AssetDownloadResult("failed", candidate.url, error="empty", method=method)

    def _validate(self, candidate: AssetCandidate, result: AssetDownloadResult) -> AssetDownloadResult:
        data = result.data or b""
        result.size_bytes = len(data)
        if result.status != "done":
            return result
        if candidate.type == "pdf":
            if not _is_pdf(data, result.content_type):
                result.status = "failed"
                result.error = "not_pdf"
            return result
        if _looks_like_html(data, result.content_type):
            result.status = "failed"
            result.error = "html_response"
            return result
        if candidate.type == "figure":
            if result.size_bytes < self.min_image_bytes:
                result.status = "failed"
                result.error = "too_small"
                return result
            if not result.content_type:
                result.content_type = content_type_from_data_or_url(data, candidate.url)
            return result
        return result


def _is_pdf(data: bytes, content_type: str = "") -> bool:
    return data.startswith(b"%PDF") or ("application/pdf" in (content_type or "").lower() and not _looks_like_html(data, content_type))


def _looks_like_html(data: bytes, content_type: str = "") -> bool:
    prefix = data[:200].lstrip().lower()
    return "text/html" in (content_type or "").lower() or prefix.startswith(b"<html") or prefix.startswith(b"<!doctype html")


def content_type_from_data_or_url(data: bytes, url: str) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    ext = extension_from_url_or_type(url, "")
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(ext, "application/octet-stream")


def extension_from_url_or_type(url: str, content_type: str = "") -> str:
    ctype = (content_type or "").split(";", 1)[0].lower()
    by_type = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/tiff": ".tif",
        "application/pdf": ".pdf",
    }
    if ctype in by_type:
        return by_type[ctype]
    path = urlparse(url).path.lower()
    if "." in path:
        ext = "." + path.rsplit(".", 1)[-1]
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".tif", ".tiff", ".pdf"}:
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"
