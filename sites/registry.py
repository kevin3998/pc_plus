"""Registry for supported site adapters."""

from urllib.parse import urlparse

from sites.base import SiteAdapter
from sites.nature import NatureAdapter
from sites.sciencedirect import ScienceDirectAdapter
from sites.springer import SpringerAdapter
from sites.wiley import WileyAdapter

_ADAPTERS: dict[str, SiteAdapter] = {
    "sciencedirect": ScienceDirectAdapter(),
    "springer": SpringerAdapter(),
    "nature": NatureAdapter(),
    "wiley": WileyAdapter(),
}


def supported_sites() -> list[str]:
    return list(_ADAPTERS)


def get_adapter(site: str) -> SiteAdapter:
    key = (site or "").lower()
    if key not in _ADAPTERS:
        raise KeyError(key)
    return _ADAPTERS[key]


def detect_adapter(url: str) -> SiteAdapter:
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError(f"无法识别站点: {url}")
    for adapter in _ADAPTERS.values():
        if adapter.matches_url(url):
            return adapter
    raise ValueError(f"无法识别站点: {url}")


def site_configs() -> dict[str, dict[str, str]]:
    return {
        adapter.key: {
            "name": adapter.name,
            "search_base": adapter.search_base,
            "article_pattern": "|".join(adapter.domains),
        }
        for adapter in _ADAPTERS.values()
    }
