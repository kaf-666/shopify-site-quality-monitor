import os
from urllib.parse import urlsplit


MONDRESSY_US_SITE = "mondressy_US"
MONDRESSY_US_DEFAULT_SIGNED_HOST = "mondressy.com"
MONDRESSY_US_SIGNATURE_HEADERS = (
    ("MONDRESSY_US_SHOPIFY_SIGNATURE", "Signature"),
    ("MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT", "Signature-Input"),
    ("MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT", "Signature-Agent"),
)
INTERCEPTED_REQUEST_PROFILE = {
    "request_header_injection": "route",
    "http_cache_mode": "disabled_by_routing",
    "run_profile": "intercepted_cold_context",
}
DIRECT_REQUEST_PROFILE = {
    "request_header_injection": "none",
    "http_cache_mode": "default",
    "run_profile": "direct_context",
}


def signed_request_profile(site_config):
    if _site_key(site_config) == MONDRESSY_US_SITE:
        return dict(INTERCEPTED_REQUEST_PROFILE)
    return dict(DIRECT_REQUEST_PROFILE)


def load_signed_request_headers(site_config):
    if _site_key(site_config) != MONDRESSY_US_SITE:
        return {}

    missing = [
        env_name
        for env_name, _header_name in MONDRESSY_US_SIGNATURE_HEADERS
        if not (os.environ.get(env_name) or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )

    return {
        header_name: os.environ[env_name]
        for env_name, header_name in MONDRESSY_US_SIGNATURE_HEADERS
    }


def mondressy_us_signed_hosts(site_config=None):
    config = (
        site_config
        if isinstance(site_config, dict)
        else {
            "site": MONDRESSY_US_SITE,
            "signed_request_hosts": [MONDRESSY_US_DEFAULT_SIGNED_HOST],
        }
    )
    if _site_key(config) != MONDRESSY_US_SITE:
        return frozenset()

    configured_hosts = config.get("signed_request_hosts")
    if configured_hosts is None:
        configured_hosts = [_configured_entry_host(config)]
    elif isinstance(configured_hosts, str):
        configured_hosts = [configured_hosts]

    hosts = set()
    for value in configured_hosts or []:
        host = _normalized_host(value)
        if host:
            hosts.add(host)

    if not hosts:
        raise RuntimeError(
            "Mondressy US signed_request_hosts must contain at least one host."
        )
    return frozenset(hosts)


def is_mondressy_us_signed_url(value, site_config=None):
    allowed_hosts = mondressy_us_signed_hosts(site_config)
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return False

    return bool(
        parsed.scheme.lower() in ("http", "https")
        and (parsed.hostname or "").lower() in allowed_hosts
    )


def install_signed_request_routing(context, site_config):
    headers = load_signed_request_headers(site_config)
    if not headers:
        return signed_request_profile(site_config)
    allowed_hosts = mondressy_us_signed_hosts(site_config)

    def inject_headers(route):
        original_headers = dict(route.request.all_headers())
        signed_header_names = {
            header_name.lower()
            for _env_name, header_name in MONDRESSY_US_SIGNATURE_HEADERS
        }
        merged_headers = {
            key: value
            for key, value in original_headers.items()
            if key.lower() not in signed_header_names
        }
        merged_headers.update(headers)
        try:
            route.continue_(headers=merged_headers)
        except Exception:
            raise RuntimeError(
                "Mondressy US signed request header injection failed."
            ) from None

    context.route(
        lambda value: _url_matches_hosts(value, allowed_hosts),
        inject_headers,
    )
    return signed_request_profile(site_config)


def _site_key(site_config):
    if not isinstance(site_config, dict):
        return None
    return site_config.get("site")


def _configured_entry_host(site_config):
    base_url = site_config.get("base_url")
    if not base_url:
        pages = site_config.get("pages") or {}
        home = pages.get("home") or {}
        base_url = home.get("url")
    return _normalized_host(base_url)


def _normalized_host(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text if "://" in text else f"https://{text}")
    except ValueError:
        return None
    return (parsed.hostname or "").lower() or None


def _url_matches_hosts(value, allowed_hosts):
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() in ("http", "https")
        and (parsed.hostname or "").lower() in allowed_hosts
    )
