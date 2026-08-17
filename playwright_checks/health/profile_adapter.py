from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright_checks.health.capabilities import (
    CAPABILITY_RISKS,
    PAGE_CAPABILITY_UNIVERSE,
    ConfigCapabilityDetector,
    canonical_capability_name,
    infer_page_type,
)
from playwright_checks.health.config import get_health_check_config
from playwright_checks.health.interaction_policy import InteractionPolicy
from playwright_checks.health.models import PageType, SideEffectLevel
from playwright_checks.health.site_profile import (
    CapabilityScope,
    CapabilityStatus,
    ProfileCapability,
    ProfileInteractionPolicy,
    ProfilePage,
    ProfileSource,
    SiteIdentity,
    SitePlatform,
    SiteProfile,
    SiteType,
)


REGION_DEFAULTS = {
    "US": ("en-US", "US"),
    "UK": ("en-GB", "UK"),
}


OBSERVATION_CAPABILITIES = {
    "product_content": ("product_title", "product_price"),
    "product_count": ("product_grid",),
    "pagination": ("pagination",),
    "add_to_cart_state": ("add_to_cart_control",),
    "variant_selection": ("variant_selector",),
}


class LegacySiteConfigAdapter:
    """Convert existing optional-field YAML mappings into SiteProfile v1."""

    name = "legacy_site_config_adapter"
    version = "1.0"

    def __init__(self, site_config, health_config=None, observations=None):
        if not isinstance(site_config, dict):
            raise TypeError("site_config must be a mapping")
        self.site_config = site_config
        self.health_config = health_config or get_health_check_config(
            site_config
        )
        self.observations = [
            dict(value)
            for value in (observations or [])
            if isinstance(value, dict)
        ]
        self.detector = ConfigCapabilityDetector()
        self.profile_config = _mapping(
            site_config.get("site_profile"),
            "site_profile",
        )
        self.interaction = InteractionPolicy(
            self.health_config.get("interaction_policy", {})
        )

    def build(self, generated_at=None):
        identity = self._identity()
        pages = self._pages()
        page_capabilities = []
        for page in pages:
            page_capabilities.extend(self._page_capabilities(page))
        site_capabilities = self._site_capabilities(page_capabilities)
        interaction_profile = ProfileInteractionPolicy(
            allowed_levels=[
                level
                for level in SideEffectLevel
                if level in self.interaction.allowed_levels
            ],
            transactional_requires_explicit_opt_in=(
                self.interaction.transactional_requires_opt_in
            ),
            high_risk_allowed=self.interaction.high_risk_allowed,
            capability_overrides=dict(self.interaction.capability_overrides),
        )
        profile = SiteProfile(
            profile_id=f"{identity.site_id}:site-profile-v1",
            site_identity=identity,
            pages=pages,
            capabilities=site_capabilities + page_capabilities,
            interaction_policy=interaction_profile,
            generated_at=generated_at or self.profile_config.get("generated_at")
            or _generated_at(),
            metadata={
                "adapter": self.name,
                "adapter_version": self.version,
                "config_source": _config_source(
                    self.site_config.get("_config_path")
                ),
                "ai_used": False,
                "discovery_used": False,
                "knowledge_sources": sorted(
                    {
                        capability.source.value
                        for capability in site_capabilities + page_capabilities
                    }
                    | {page.source.value for page in pages}
                ),
            },
        )
        profile.validate()
        return profile

    def _identity(self):
        configured = _mapping(
            self.profile_config.get("site_identity"),
            "site_profile.site_identity",
        )
        site_id = str(
            configured.get("site_id")
            or self.site_config.get("site")
            or _config_stem(self.site_config.get("_config_path"))
            or "unknown"
        ).strip()
        base_url = str(
            configured.get("base_url")
            or self.site_config.get("base_url")
            or self._home_origin()
            or ""
        ).strip()
        page_types = {
            _profile_page_type(name, value)
            for name, value in (self.site_config.get("pages") or {}).items()
            if isinstance(value, dict)
        }
        inferred_site_type = (
            SiteType.ECOMMERCE
            if page_types & {PageType.PLP, PageType.PDP, PageType.CART}
            else SiteType.CONTENT
            if page_types & {PageType.CONTENT}
            else SiteType.UNKNOWN
        )
        site_type = _enum(
            SiteType,
            configured.get("site_type", inferred_site_type.value),
            "site_profile.site_identity.site_type",
        )
        platform_value = configured.get("platform")
        platform = (
            _enum(
                SitePlatform,
                platform_value,
                "site_profile.site_identity.platform",
            )
            if platform_value is not None
            else self._infer_platform()
        )
        suffix = site_id.rsplit("_", 1)[-1].upper()
        default_locale, default_region = REGION_DEFAULTS.get(
            suffix,
            (None, None),
        )
        locale = _optional_text(
            configured.get(
                "locale",
                self.site_config.get("locale", default_locale),
            )
        )
        region = _optional_text(
            configured.get(
                "region",
                self.site_config.get("region", default_region),
            )
        )
        return SiteIdentity(
            site_id=site_id,
            base_url=base_url,
            site_type=site_type,
            platform=platform,
            locale=locale,
            region=region,
            metadata={
                "field_sources": {
                    "site_id": (
                        "MANUAL" if "site_id" in configured else "CONFIG"
                    ),
                    "base_url": (
                        "MANUAL"
                        if "base_url" in configured
                        else "CONFIG"
                        if self.site_config.get("base_url")
                        else "INFERRED"
                    ),
                    "site_type": (
                        "MANUAL" if "site_type" in configured else "INFERRED"
                    ),
                    "platform": (
                        "MANUAL" if platform_value is not None else "INFERRED"
                    ),
                    "locale": (
                        "MANUAL"
                        if "locale" in configured
                        else "CONFIG"
                        if self.site_config.get("locale")
                        else "INFERRED"
                    ),
                    "region": (
                        "MANUAL"
                        if "region" in configured
                        else "CONFIG"
                        if self.site_config.get("region")
                        else "INFERRED"
                    ),
                }
            },
        )

    def _pages(self):
        legacy_pages = _mapping(self.site_config.get("pages"), "pages")
        configured_pages = _mapping(
            self.profile_config.get("pages"),
            "site_profile.pages",
        )
        keys = list(legacy_pages)
        keys.extend(sorted(key for key in configured_pages if key not in legacy_pages))
        pages = []
        for key in keys:
            legacy = legacy_pages.get(key) or {}
            override = configured_pages.get(key) or {}
            if not isinstance(legacy, dict):
                raise TypeError(f"pages.{key} must be a mapping")
            if not isinstance(override, dict):
                raise TypeError(f"site_profile.pages.{key} must be a mapping")
            url = str(override.get("url") or legacy.get("url") or "").strip()
            page_type = _profile_page_type(
                key,
                {**legacy, **override},
            )
            default_source = (
                ProfileSource.CONFIG
                if key in legacy_pages
                else ProfileSource.MANUAL
            )
            source = _enum(
                ProfileSource,
                override.get("source", default_source.value),
                f"site_profile.pages.{key}.source",
            )
            confidence = _profile_confidence(
                override.get("confidence", 1.0),
                f"site_profile.pages.{key}.confidence",
            )
            pages.append(
                ProfilePage(
                    page_id=str(override.get("page_id") or key),
                    page_type=page_type,
                    url=url,
                    source=source,
                    confidence=confidence,
                    enabled=_profile_bool(
                        override.get("enabled", legacy.get("enabled", True)),
                        f"site_profile.pages.{key}.enabled",
                    ),
                    representative=_profile_bool(
                        override.get("representative", True),
                        f"site_profile.pages.{key}.representative",
                    ),
                    metadata={
                        "config_key": key,
                        "legacy_adapter": key in legacy_pages,
                        **_mapping(
                            override.get("metadata"),
                            f"site_profile.pages.{key}.metadata",
                        ),
                    },
                )
            )
        return pages

    def _page_capabilities(self, page):
        config_key = page.metadata.get("config_key") or page.page_id
        page_config = (
            (self.site_config.get("pages") or {}).get(config_key) or {}
        )
        detected = {}
        if page_config:
            detector_profile = self.detector.detect(config_key, page_config)
            for signal in detector_profile.capabilities:
                name = canonical_capability_name(signal.name)
                detected[name] = {
                    "source": _signal_source(signal.source),
                    "confidence": signal.confidence,
                    "selector_hint": _selector_hint(
                        page_config,
                        name,
                    ),
                    "metadata": {
                        "detector_source": signal.source,
                        "detector_evidence": signal.evidence,
                    },
                }
        detected.setdefault(
            "navigation",
            {
                "source": ProfileSource.INFERRED,
                "confidence": 0.95,
                "selector_hint": None,
                "metadata": {"reason": "configured_representative_url"},
            },
        )
        for capability, observation_count in self._observation_signals(
            page
        ).items():
            value = detected.setdefault(
                capability,
                {
                    "source": ProfileSource.INFERRED,
                    "confidence": 0.9,
                    "selector_hint": _selector_hint(
                        page_config,
                        capability,
                    ),
                    "metadata": {},
                },
            )
            value["metadata"]["passed_observation_count"] = observation_count

        overrides = self._page_capability_overrides(page)
        universe = {
            canonical_capability_name(name)
            for name in PAGE_CAPABILITY_UNIVERSE.get(page.page_type, ())
        }
        universe.update(detected)
        universe.update(canonical_capability_name(name) for name in overrides)
        capabilities = []
        for name in sorted(universe):
            signal = detected.get(name)
            status = (
                CapabilityStatus.PRESENT
                if signal
                else CapabilityStatus.UNKNOWN
            )
            source = (
                signal["source"] if signal else ProfileSource.INFERRED
            )
            confidence = signal["confidence"] if signal else 0.0
            selector_hint = (
                signal.get("selector_hint") if signal else None
            )
            metadata = dict(signal.get("metadata") or {}) if signal else {
                "reason": "not_confirmed_by_current_config_rules"
            }
            raw_override = overrides.get(name)
            if raw_override is not None:
                override = _capability_override(
                    raw_override,
                    f"site_profile.capabilities.pages.{page.page_id}.{name}",
                )
                status = override["status"]
                source = override["source"]
                confidence = override["confidence"]
                selector_hint = override.get(
                    "selector_hint",
                    selector_hint,
                )
                metadata.update(override.get("metadata") or {})
                configured_level = override.get("interaction_policy")
            else:
                configured_level = None
            capability = self._capability(
                name=name,
                scope=CapabilityScope.PAGE,
                status=status,
                source=source,
                confidence=confidence,
                page=page,
                selector_hint=selector_hint,
                metadata=metadata,
                configured_level=configured_level,
            )
            capabilities.append(capability)
        return capabilities

    def _site_capabilities(self, page_capabilities):
        grouped = defaultdict(list)
        for capability in page_capabilities:
            grouped[capability.name].append(capability)
        overrides = self._site_capability_overrides()
        names = set(grouped) | set(overrides)
        capabilities = []
        for name in sorted(names):
            values = grouped.get(name, [])
            status = _aggregate_status(
                [value.status for value in values]
            )
            selected = _selected_capability(values, status)
            source = (
                selected.source if selected else ProfileSource.INFERRED
            )
            confidence = selected.confidence if selected else 0.0
            metadata = {
                "page_statuses": {
                    value.page_id: value.status.value for value in values
                }
            }
            raw_override = overrides.get(name)
            if raw_override is not None:
                override = _capability_override(
                    raw_override,
                    f"site_profile.capabilities.site.{name}",
                )
                status = override["status"]
                source = override["source"]
                confidence = override["confidence"]
                metadata.update(override.get("metadata") or {})
                configured_level = override.get("interaction_policy")
                selector_hint = override.get("selector_hint")
            else:
                configured_level = None
                selector_hint = None
            capabilities.append(
                self._capability(
                    name=name,
                    scope=CapabilityScope.SITE,
                    status=status,
                    source=source,
                    confidence=confidence,
                    page=None,
                    selector_hint=selector_hint,
                    metadata=metadata,
                    configured_level=configured_level,
                )
            )
        return capabilities

    def _capability(
        self,
        name,
        scope,
        status,
        source,
        confidence,
        page,
        selector_hint,
        metadata,
        configured_level=None,
    ):
        default_level = configured_level or CAPABILITY_RISKS.get(
            name,
            SideEffectLevel.HIGH_RISK,
        )
        level = self.interaction.level_for(name, default_level)
        decision = self.interaction.decide(name, level=level)
        return ProfileCapability(
            name=name,
            scope=scope,
            status=status,
            source=source,
            confidence=float(confidence),
            interaction_policy=level,
            default_interaction_allowed=decision.allowed,
            interaction_reason=decision.reason,
            page_type=page.page_type if page else None,
            page_id=page.page_id if page else None,
            selector_hint=selector_hint,
            metadata=metadata,
        )

    def _observation_signals(self, page):
        observed = defaultdict(int)
        config_key = page.metadata.get("config_key") or page.page_id
        for result in self.observations:
            if result.get("result_type") != "deterministic_check":
                continue
            if result.get("status") != "passed":
                continue
            if str(result.get("page") or "") not in {
                page.page_id,
                str(config_key),
            }:
                continue
            capability = canonical_capability_name(
                result.get("capability")
            )
            names = OBSERVATION_CAPABILITIES.get(
                str(result.get("case") or ""),
                (capability,) if capability else (),
            )
            for name in names:
                observed[canonical_capability_name(name)] += 1
        return observed

    def _page_capability_overrides(self, page):
        capabilities = _mapping(
            self.profile_config.get("capabilities"),
            "site_profile.capabilities",
        )
        pages = _mapping(
            capabilities.get("pages"),
            "site_profile.capabilities.pages",
        )
        config_key = page.metadata.get("config_key")
        raw = pages.get(page.page_id)
        if raw is None and config_key:
            raw = pages.get(config_key)
        return _canonical_mapping(
            raw,
            f"site_profile.capabilities.pages.{page.page_id}",
        )

    def _site_capability_overrides(self):
        capabilities = _mapping(
            self.profile_config.get("capabilities"),
            "site_profile.capabilities",
        )
        return _canonical_mapping(
            capabilities.get("site"),
            "site_profile.capabilities.site",
        )

    def _home_origin(self):
        pages = self.site_config.get("pages") or {}
        home = pages.get("home") or {}
        url = home.get("url") if isinstance(home, dict) else None
        if not url:
            return None
        parsed = urlsplit(str(url))
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    def _infer_platform(self):
        urls = [
            str(value.get("url") or "").lower()
            for value in (self.site_config.get("pages") or {}).values()
            if isinstance(value, dict)
        ]
        if any("/collections/" in url for url in urls) and any(
            "/products/" in url for url in urls
        ):
            return SitePlatform.SHOPIFY
        return SitePlatform.UNKNOWN


def _profile_page_type(name, config):
    configured = config.get("page_type") if isinstance(config, dict) else None
    if configured is not None:
        return _enum(
            PageType,
            configured,
            f"pages.{name}.page_type",
        )
    inferred = infer_page_type(name, config if isinstance(config, dict) else {})
    return PageType.OTHER if inferred == PageType.UNKNOWN else inferred


def _signal_source(value):
    normalized = str(value or "").lower()
    if normalized in {
        "modules",
        "selectors",
        "plugins",
        "page_config",
        "declared_capability",
        "variant_check",
        "dom_presence",
        "interaction_or_presence_config",
        "selector_semantics",
    }:
        return ProfileSource.CONFIG
    return ProfileSource.INFERRED


def _selector_hint(page_config, capability):
    modules = page_config.get("modules", {}) or {}
    presence = page_config.get("dom_presence", {}) or {}
    content_checks = page_config.get("content_checks", {}) or {}
    product_content_fallbacks = {
        "product_title": [
            "css",
            (
                "h1, .product-single__title, .product__title, "
                "[class*='product-title'], [class*='product__title']"
            ),
        ],
        "product_price": [
            "css",
            (
                ".price, .product__price, .product-single__price, "
                "[class*='price']"
            ),
        ],
    }
    product_card = page_config.get("product_card") or modules.get(
        "product_grid"
    )
    multiple_modules = {
        "kind": "multiple_signals",
        "mode": "visible",
        "signals": dict(modules),
    }
    multiple_presence = {
        "kind": "multiple_signals",
        "mode": "attached",
        "signals": dict(presence),
    }
    multiple_plugins = {
        "kind": "multiple_signals",
        "mode": "visible",
        "signals": dict(page_config.get("plugins", {}) or {}),
    }

    def descendants(selector):
        return {
            "kind": "descendant_presence",
            "root": product_card,
            "descendant": selector,
            "sample_limit": 12,
            "required_ratio": 0.8,
        }

    mapping = {
        "navigation": next(
            (
                modules[name]
                for name in modules
                if str(name).startswith("header")
            ),
            None,
        ),
        "core_modules": multiple_modules if modules else None,
        "presence_signals": multiple_presence if presence else None,
        "plugin_signals": (
            multiple_plugins if multiple_plugins["signals"] else None
        ),
        "main_content": "main, #MainContent, #main-content, [role='main']",
        "hero": modules.get("banner"),
        "mobile_menu": (
            ((page_config.get("readonly_interactions") or {}).get(
                "mobile_menu"
            ) or {}).get("trigger")
            or next(
                (
                    value
                    for name, value in presence.items()
                    if "mobile" in str(name).lower()
                    and "nav" in str(name).lower()
                ),
                None,
            )
        ),
        "search": modules.get("search_btn"),
        "product_grid": product_card,
        "product_card": product_card,
        "product_card_title": descendants(
            "h2, h3, [class*='title'], [class*='name'], "
            "a[href*='/products/']"
        ),
        "product_card_price": descendants(
            ".price, [class*='price'], [data-product-price]"
        ),
        "product_card_image": descendants("img, picture img"),
        "filter": modules.get("filter"),
        "sort": modules.get("sort") or modules.get("filter"),
        "pagination": modules.get("pagination"),
        "product_gallery": modules.get("gallery"),
        "product_main_image": {
            "kind": "descendant_presence",
            "root": modules.get("gallery"),
            "descendant": "img, picture img",
            "sample_limit": 1,
            "required_ratio": 1.0,
        },
        "product_info": modules.get("info"),
        "product_form": "form[action*='/cart/add']",
        "product_title": content_checks.get("title")
        or product_content_fallbacks["product_title"],
        "product_price": content_checks.get("price")
        or product_content_fallbacks["product_price"],
        "variant_selector": page_config.get("variant_inputs"),
        "size_selector": page_config.get("variant_inputs"),
        "color_selector": page_config.get("variant_inputs"),
        "add_to_cart": modules.get("add_to_cart"),
        "add_to_cart_control": modules.get("add_to_cart"),
        "add_to_cart_action": modules.get("add_to_cart"),
        "cart_drawer": modules.get("cart_btn"),
        "reviews": modules.get("reviews"),
        "modal": next(
            (
                value
                for name, value in presence.items()
                if "modal" in str(name).lower()
            ),
            None,
        ),
    }
    return mapping.get(capability)


def _aggregate_status(statuses):
    values = list(statuses)
    if not values:
        return CapabilityStatus.UNKNOWN
    if CapabilityStatus.PRESENT in values:
        return CapabilityStatus.PRESENT
    if all(value == CapabilityStatus.ABSENT for value in values):
        return CapabilityStatus.ABSENT
    if all(value == CapabilityStatus.NOT_APPLICABLE for value in values):
        return CapabilityStatus.NOT_APPLICABLE
    return CapabilityStatus.UNKNOWN


def _selected_capability(values, status):
    candidates = [value for value in values if value.status == status]
    if not candidates:
        candidates = list(values)
    return max(candidates, key=lambda value: value.confidence, default=None)


def _capability_override(value, path):
    if isinstance(value, str):
        value = {"status": value}
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be a status string or mapping")
    status = _enum(
        CapabilityStatus,
        value.get("status", "UNKNOWN"),
        f"{path}.status",
    )
    default_confidence = 0.0 if status == CapabilityStatus.UNKNOWN else 1.0
    source = _enum(
        ProfileSource,
        value.get("source", "MANUAL"),
        f"{path}.source",
    )
    interaction = value.get("interaction_policy")
    result = {
        "status": status,
        "source": source,
        "confidence": _profile_confidence(
            value.get("confidence", default_confidence),
            f"{path}.confidence",
        ),
        "metadata": _mapping(value.get("metadata"), f"{path}.metadata"),
        "interaction_policy": (
            _enum(
                SideEffectLevel,
                interaction,
                f"{path}.interaction_policy",
            )
            if interaction is not None
            else None
        ),
    }
    if "selector_hint" in value:
        result["selector_hint"] = value["selector_hint"]
    return result


def _canonical_mapping(value, path):
    raw = _mapping(value, path)
    return {
        canonical_capability_name(name): item
        for name, item in raw.items()
    }


def _enum(enum_type, value, path):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value).strip().upper())
    except (TypeError, ValueError) as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{path} must be one of {allowed}") from error


def _mapping(value, path):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be a mapping")
    return dict(value)


def _profile_confidence(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{path} must be a number")
    normalized = float(value)
    if not 0 <= normalized <= 1:
        raise ValueError(f"{path} must be between 0 and 1")
    return normalized


def _profile_bool(value, path):
    if not isinstance(value, bool):
        raise TypeError(f"{path} must be a boolean")
    return value


def _optional_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _config_stem(value):
    return Path(str(value)).stem if value else None


def _config_source(value):
    return f"configs/sites/{Path(str(value)).name}" if value else None


def _generated_at():
    from playwright_checks.health.models import utc_timestamp

    return utc_timestamp()
