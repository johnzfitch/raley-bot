"""Shared domain allowlist helpers."""


def is_raleys_domain(domain: str) -> bool:
    """Check if domain is exactly raleys.com or a subdomain of it."""
    normalized = domain.lstrip(".").lower()
    return normalized == "raleys.com" or normalized.endswith(".raleys.com")
