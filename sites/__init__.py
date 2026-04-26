"""Site adapters for supported scholarly platforms."""

from sites.registry import detect_adapter, get_adapter, site_configs, supported_sites

__all__ = ["detect_adapter", "get_adapter", "site_configs", "supported_sites"]
