"""Nature adapter skeleton."""

from urllib.parse import urlparse

from sites.base import SiteAdapter


class NatureAdapter(SiteAdapter):
    key = "nature"
    name = "Nature"
    domains = ("nature.com", "www.nature.com")
    search_base = "https://www.nature.com/search"
    login_url = "https://www.nature.com"
    article_domain = "www.nature.com"
    supports_search = False

    def normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        if "nature.com" not in parsed.netloc:
            return url
        path = parsed.path.rstrip("/")
        if not path.startswith("/articles/"):
            return ""
        return f"https://www.nature.com{path}"

    def search(self, engine, query: str, year_from: int = 2024, year_to: int = 2025, max_results: int = 200):
        raise NotImplementedError("nature adapter is registered but search is not implemented yet")
