import os
from urllib.parse import urlsplit


MONDRESSY_US_SITE = "mondressy_US"
MONDRESSY_US_SIGNED_HOSTS = frozenset(
    {
        "mondressy.com",
        "www.mondressy.com",
    }
)
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


def is_mondressy_us_signed_url(value):
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return False

    return bool(
        parsed.scheme.lower() in ("http", "https")
        and (parsed.hostname or "").lower() in MONDRESSY_US_SIGNED_HOSTS
    )


def install_signed_request_routing(context, site_config):
    headers = load_signed_request_headers(site_config)
    if not headers:
        return signed_request_profile(site_config)

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

    context.route(is_mondressy_us_signed_url, inject_headers)
    return signed_request_profile(site_config)


def _site_key(site_config):
    if not isinstance(site_config, dict):
        return None
    return site_config.get("site")
