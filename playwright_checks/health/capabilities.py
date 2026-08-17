from urllib.parse import urlsplit

from playwright_checks.health.models import (
    CapabilitySignal,
    PageCapabilityProfile,
    PageType,
    SideEffectLevel,
)


PAGE_NAME_TYPES = {
    "home": PageType.HOME,
    "collection": PageType.PLP,
    "plp": PageType.PLP,
    "product": PageType.PDP,
    "pdp": PageType.PDP,
    "search": PageType.SEARCH,
    "cart": PageType.CART,
    "login": PageType.LOGIN,
    "account": PageType.ACCOUNT,
    "checkout": PageType.CHECKOUT_ENTRY,
    "checkout_entry": PageType.CHECKOUT_ENTRY,
    "content": PageType.CONTENT,
    "other": PageType.OTHER,
}


CAPABILITY_RISKS = {
    "navigation": SideEffectLevel.SAFE,
    "core_modules": SideEffectLevel.SAFE,
    "presence_signals": SideEffectLevel.SAFE,
    "plugin_signals": SideEffectLevel.SAFE,
    "mega_menu": SideEffectLevel.SAFE,
    "main_content": SideEffectLevel.SAFE,
    "hero": SideEffectLevel.SAFE,
    "product_listing": SideEffectLevel.SAFE,
    "product_grid": SideEffectLevel.SAFE,
    "product_card": SideEffectLevel.SAFE,
    "product_card_title": SideEffectLevel.SAFE,
    "product_card_price": SideEffectLevel.SAFE,
    "product_card_image": SideEffectLevel.SAFE,
    "product_recommendations": SideEffectLevel.SAFE,
    "search": SideEffectLevel.SAFE,
    "filter": SideEffectLevel.SAFE,
    "sort": SideEffectLevel.SAFE,
    "pagination": SideEffectLevel.SAFE,
    "load_more": SideEffectLevel.SAFE,
    "gallery": SideEffectLevel.SAFE,
    "product_gallery": SideEffectLevel.SAFE,
    "product_main_image": SideEffectLevel.SAFE,
    "product_info": SideEffectLevel.SAFE,
    "product_form": SideEffectLevel.SAFE,
    "variant": SideEffectLevel.SAFE,
    "variant_selector": SideEffectLevel.SAFE,
    "size": SideEffectLevel.SAFE,
    "size_selector": SideEffectLevel.SAFE,
    "color": SideEffectLevel.SAFE,
    "color_selector": SideEffectLevel.SAFE,
    "product_title": SideEffectLevel.SAFE,
    "product_price": SideEffectLevel.SAFE,
    "product_status": SideEffectLevel.SAFE,
    "quantity": SideEffectLevel.SAFE,
    "quantity_selector": SideEffectLevel.SAFE,
    "reviews": SideEffectLevel.SAFE,
    "shipping_information": SideEffectLevel.SAFE,
    "shipping_info": SideEffectLevel.SAFE,
    "recommendations": SideEffectLevel.SAFE,
    "mobile_menu": SideEffectLevel.SAFE,
    "drawer": SideEffectLevel.SAFE,
    "modal": SideEffectLevel.SAFE,
    "popup": SideEffectLevel.SAFE,
    "accordion": SideEffectLevel.SAFE,
    "tab": SideEffectLevel.SAFE,
    "localization": SideEffectLevel.SAFE,
    "wishlist": SideEffectLevel.SAFE,
    # Kept for backwards-compatible profile overrides. New plans split the
    # read-only control observation from the state-changing cart action.
    "add_to_cart": SideEffectLevel.TRANSACTIONAL_SAFE,
    "add_to_cart_control": SideEffectLevel.SAFE,
    "add_to_cart_action": SideEffectLevel.TRANSACTIONAL_SAFE,
    "cart_drawer": SideEffectLevel.TRANSACTIONAL_SAFE,
    "cart_page": SideEffectLevel.SAFE,
    "cart_quantity": SideEffectLevel.TRANSACTIONAL_SAFE,
    "cart_remove": SideEffectLevel.TRANSACTIONAL_SAFE,
    "buy_now": SideEffectLevel.HIGH_RISK,
    "login": SideEffectLevel.HIGH_RISK,
    "account": SideEffectLevel.HIGH_RISK,
    "checkout": SideEffectLevel.HIGH_RISK,
    "form_submit": SideEffectLevel.HIGH_RISK,
    "checkout_entry": SideEffectLevel.HIGH_RISK,
    "checkout_submit": SideEffectLevel.HIGH_RISK,
    "payment": SideEffectLevel.HIGH_RISK,
    "submit_order": SideEffectLevel.HIGH_RISK,
    "account_creation": SideEffectLevel.HIGH_RISK,
    "real_form_submission": SideEffectLevel.HIGH_RISK,
}


CAPABILITY_ALIASES = {
    "product_listing": "product_grid",
    "gallery": "product_gallery",
    "variant": "variant_selector",
    "size": "size_selector",
    "color": "color_selector",
    "quantity": "quantity_selector",
    "shipping_information": "shipping_info",
    "recommendations": "product_recommendations",
}


PAGE_CAPABILITY_UNIVERSE = {
    PageType.HOME: (
        "navigation",
        "core_modules",
        "presence_signals",
        "plugin_signals",
        "main_content",
        "hero",
        "mobile_menu",
        "mega_menu",
        "search",
        "product_recommendations",
        "cart_drawer",
        "modal",
        "popup",
    ),
    PageType.PLP: (
        "navigation",
        "core_modules",
        "presence_signals",
        "search",
        "product_grid",
        "product_card",
        "product_card_title",
        "product_card_price",
        "product_card_image",
        "filter",
        "sort",
        "pagination",
        "load_more",
        "cart_drawer",
        "modal",
        "popup",
    ),
    PageType.PDP: (
        "navigation",
        "core_modules",
        "presence_signals",
        "search",
        "product_title",
        "product_price",
        "product_gallery",
        "product_main_image",
        "product_info",
        "product_form",
        "size_selector",
        "color_selector",
        "variant_selector",
        "quantity_selector",
        "add_to_cart_control",
        "add_to_cart_action",
        "buy_now",
        "cart_drawer",
        "reviews",
        "shipping_info",
        "accordion",
        "modal",
        "popup",
    ),
    PageType.SEARCH: (
        "navigation",
        "search",
        "product_grid",
        "filter",
        "sort",
        "pagination",
        "load_more",
    ),
    PageType.CART: (
        "navigation",
        "cart_page",
        "cart_quantity",
        "cart_remove",
    ),
    PageType.LOGIN: (
        "navigation",
        "login",
        "account_creation",
    ),
    PageType.ACCOUNT: (
        "navigation",
        "account",
    ),
    PageType.CHECKOUT_ENTRY: (
        "navigation",
        "checkout_entry",
        "checkout_submit",
        "payment",
        "submit_order",
    ),
    PageType.CONTENT: (
        "navigation",
        "accordion",
        "modal",
        "popup",
    ),
    PageType.OTHER: (
        "navigation",
        "accordion",
        "modal",
        "popup",
    ),
}


CANONICAL_CAPABILITIES = tuple(
    sorted(
        set(CAPABILITY_ALIASES.values())
        | {
            capability
            for capabilities in PAGE_CAPABILITY_UNIVERSE.values()
            for capability in capabilities
        }
        | set(CAPABILITY_RISKS)
    )
)


PROTECTED_HIGH_RISK_ACTIONS = frozenset(
    {
        "payment",
        "submit_order",
        "checkout_submit",
        "account_creation",
        "real_form_submission",
        "form_submit",
    }
)


def canonical_capability_name(value):
    normalized = str(value or "").strip().lower()
    return CAPABILITY_ALIASES.get(normalized, normalized)


def infer_page_type(page_name, page_config=None):
    normalized = str(page_name or "").strip().lower()
    if normalized in PAGE_NAME_TYPES:
        return PAGE_NAME_TYPES[normalized]

    path = ""
    try:
        path = urlsplit(str((page_config or {}).get("url") or "")).path.lower()
    except ValueError:
        pass
    if "/products/" in path:
        return PageType.PDP
    if "/collections/" in path:
        return PageType.PLP
    if path.startswith("/search"):
        return PageType.SEARCH
    if path.startswith("/cart"):
        return PageType.CART
    if path.startswith("/account/login"):
        return PageType.LOGIN
    if path.startswith("/account"):
        return PageType.ACCOUNT
    if path.startswith("/checkout"):
        return PageType.CHECKOUT_ENTRY
    if path in ("", "/"):
        return PageType.HOME
    return PageType.UNKNOWN


class ConfigCapabilityDetector:
    """Derive a deterministic page profile from reviewed site configuration.

    This is the first detector in the chain.  A future discovery detector can
    add live DOM or AI signals without changing the execution engine contract.
    """

    name = "config_capability_detector"
    version = "1.0"

    def detect(self, page_name, page_config=None):
        config = page_config or {}
        page_type = infer_page_type(page_name, config)
        signals = {}

        def add(name, source, evidence=None, confidence=1.0, risk=None):
            signals[name] = CapabilitySignal(
                name=name,
                side_effect_level=(
                    risk or CAPABILITY_RISKS.get(name, SideEffectLevel.SAFE)
                ),
                detected=True,
                source=source,
                confidence=float(confidence),
                evidence=dict(evidence or {}),
            )

        modules = config.get("modules", {}) or {}
        interactions = config.get("readonly_interactions", {}) or {}
        plugins = config.get("plugins", {}) or {}
        presence = config.get("dom_presence", {}) or {}

        if modules and page_type in (PageType.HOME, PageType.PLP, PageType.PDP):
            add(
                "core_modules",
                "modules",
                {"modules": sorted(str(name) for name in modules)},
            )
        if presence and page_type in (PageType.HOME, PageType.PLP, PageType.PDP):
            add(
                "presence_signals",
                "dom_presence",
                {"entries": sorted(str(name) for name in presence)},
            )
        if plugins and page_type == PageType.HOME:
            add(
                "plugin_signals",
                "plugins",
                {"plugins": sorted(str(name) for name in plugins)},
            )

        if page_type == PageType.HOME:
            add("main_content", "page_type_contract", confidence=0.8)
            if any(name.startswith("header") for name in modules):
                add("navigation", "modules", {"modules": _matching(modules, "header")})
            if "banner" in modules:
                add("hero", "modules", {"module": "banner"})
            if any(name.startswith("collections") for name in modules):
                add(
                    "product_recommendations",
                    "modules",
                    {"modules": _matching(modules, "collections")},
                )
        elif page_type == PageType.PLP:
            if "product_grid" in modules or config.get("product_card"):
                add(
                    "product_listing",
                    "selectors",
                    {"module": "product_grid", "product_card": bool(config.get("product_card"))},
                )
                add(
                    "product_card",
                    "selectors",
                    {"selector_key": "product_card"},
                )
                for signal_name in (
                    "product_card_title",
                    "product_card_price",
                    "product_card_image",
                ):
                    add(
                        signal_name,
                        "selector_semantics",
                        {"root_selector_key": "product_card"},
                        confidence=0.8,
                    )
            if "filter" in modules:
                add("filter", "modules", {"module": "filter"})
                selector_text = str(modules.get("filter", "")).lower()
                if "sort" in selector_text:
                    add("sort", "selector_semantics", {"module": "filter"}, 0.8)
            if "sort" in modules:
                add("sort", "modules", {"module": "sort"})
            if "pagination" in modules:
                add("pagination", "modules", {"module": "pagination"})
        elif page_type == PageType.PDP:
            add("product_title", "page_type_contract", confidence=0.8)
            add("product_price", "page_type_contract", confidence=0.8)
            add("product_status", "page_type_contract", confidence=0.7)
            if "gallery" in modules:
                add("gallery", "modules", {"module": "gallery"})
                add(
                    "product_main_image",
                    "selector_semantics",
                    {"root_module": "gallery"},
                    confidence=0.85,
                )
            if "info" in modules:
                add("product_info", "modules", {"module": "info"})
            add(
                "product_form",
                "page_type_contract",
                {"selector_fallback": "form[action*='/cart/add']"},
                confidence=0.75,
            )
            if config.get("variant_inputs"):
                add("variant", "selectors", {"selector_key": "variant_inputs"})
                variant_check = config.get("variant_check") or {}
                option_name = str(variant_check.get("option_name") or "").lower()
                if option_name == "size":
                    add("size", "variant_check", {"option_name": "Size"})
                if option_name == "color":
                    add("color", "variant_check", {"option_name": "Color"})
            if "add_to_cart" in modules:
                add(
                    "add_to_cart",
                    "modules",
                    {
                        "module": "add_to_cart",
                        "compatibility_only": True,
                    },
                    risk=SideEffectLevel.TRANSACTIONAL_SAFE,
                )
                add(
                    "add_to_cart_control",
                    "modules",
                    {"module": "add_to_cart", "observation_only": True},
                )
                add(
                    "add_to_cart_action",
                    "modules",
                    {"module": "add_to_cart", "state_changing": True},
                    risk=SideEffectLevel.TRANSACTIONAL_SAFE,
                )
            if config.get("accelerated_checkout"):
                add(
                    "buy_now",
                    "accelerated_checkout",
                    {"optional": True},
                    confidence=0.7,
                )
            if config.get("require_reviews"):
                add("reviews", "page_config", {"require_reviews": True})

        common_module_capabilities = {
            "search_btn": "search",
            "cart_btn": "cart_drawer",
            "user_btn": "account",
        }
        for module_name, capability in common_module_capabilities.items():
            if module_name in modules:
                add(capability, "modules", {"module": module_name})

        if "mobile_menu" in interactions or any(
            "mobile" in str(name).lower() and "nav" in str(name).lower()
            for name in presence
        ):
            add("mobile_menu", "interaction_or_presence_config")
        if any("drawer" in str(name).lower() for name in presence):
            add("drawer", "dom_presence", {"entries": list(presence)})
        if "wishlist" in plugins:
            add("wishlist", "plugins", {"plugin": "wishlist"})
        if "currency" in plugins:
            add("localization", "plugins", {"plugin": "currency"})

        _apply_declared_capabilities(signals, config.get("capabilities"))
        capabilities = sorted(signals.values(), key=lambda value: value.name)
        commerce_applicable = page_type in {
            PageType.HOME,
            PageType.PLP,
            PageType.PDP,
            PageType.CART,
            PageType.CHECKOUT_ENTRY,
        }
        return PageCapabilityProfile(
            page_type=page_type,
            capabilities=capabilities,
            detector=self.name,
            detector_version=self.version,
            commerce_applicable=commerce_applicable,
        )


def _matching(values, prefix):
    return sorted(name for name in values if str(name).startswith(prefix))


def _apply_declared_capabilities(signals, configured):
    if isinstance(configured, list):
        configured = {str(name): True for name in configured}
    if not isinstance(configured, dict):
        return
    for name, raw in configured.items():
        name = str(name).strip().lower()
        if not name:
            continue
        if raw is False or (isinstance(raw, dict) and raw.get("enabled") is False):
            signals.pop(name, None)
            continue
        options = raw if isinstance(raw, dict) else {}
        risk_value = options.get("side_effect_level")
        try:
            risk = (
                SideEffectLevel(str(risk_value).upper())
                if risk_value
                else CAPABILITY_RISKS.get(name, SideEffectLevel.SAFE)
            )
        except ValueError:
            risk = CAPABILITY_RISKS.get(name, SideEffectLevel.SAFE)
        signals[name] = CapabilitySignal(
            name=name,
            side_effect_level=risk,
            detected=True,
            source="declared_capability",
            confidence=float(options.get("confidence", 1.0)),
            evidence={
                key: value
                for key, value in options.items()
                if key not in ("enabled", "confidence", "side_effect_level")
            },
        )
