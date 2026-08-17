from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

from playwright_checks.health.capabilities import canonical_capability_name
from playwright_checks.health.models import (
    PageType,
    SideEffectLevel,
    serialize,
    utc_timestamp,
)


SITE_PROFILE_SCHEMA_VERSION = "1.0"


class SiteType(str, Enum):
    ECOMMERCE = "ECOMMERCE"
    CONTENT = "CONTENT"
    UNKNOWN = "UNKNOWN"


class SitePlatform(str, Enum):
    SHOPIFY = "SHOPIFY"
    CUSTOM = "CUSTOM"
    UNKNOWN = "UNKNOWN"


class ProfileSource(str, Enum):
    CONFIG = "CONFIG"
    DISCOVERY = "DISCOVERY"
    MANUAL = "MANUAL"
    INFERRED = "INFERRED"
    AI_INFERRED = "AI_INFERRED"


class CapabilityStatus(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CapabilityScope(str, Enum):
    SITE = "SITE"
    PAGE = "PAGE"


@dataclass
class SiteIdentity:
    site_id: str
    base_url: str
    site_type: SiteType
    platform: SitePlatform
    locale: str | None = None
    region: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProfilePage:
    page_id: str
    page_type: PageType
    url: str
    source: ProfileSource
    confidence: float
    enabled: bool = True
    representative: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProfileInteractionPolicy:
    allowed_levels: list[SideEffectLevel] = field(
        default_factory=lambda: [SideEffectLevel.SAFE]
    )
    transactional_requires_explicit_opt_in: bool = True
    high_risk_allowed: bool = False
    capability_overrides: dict[str, SideEffectLevel] = field(
        default_factory=dict
    )

    def to_config(self):
        return {
            "allowed_levels": [value.value for value in self.allowed_levels],
            "transactional_requires_explicit_opt_in": (
                self.transactional_requires_explicit_opt_in
            ),
            "high_risk_allowed": self.high_risk_allowed,
            "capability_overrides": {
                name: level.value
                for name, level in self.capability_overrides.items()
            },
        }


@dataclass
class ProfileCapability:
    name: str
    scope: CapabilityScope
    status: CapabilityStatus
    source: ProfileSource
    confidence: float
    interaction_policy: SideEffectLevel
    default_interaction_allowed: bool
    interaction_reason: str
    page_type: PageType | None = None
    page_id: str | None = None
    selector_hint: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SiteProfile:
    profile_id: str
    site_identity: SiteIdentity
    pages: list[ProfilePage]
    capabilities: list[ProfileCapability]
    interaction_policy: ProfileInteractionPolicy
    generated_at: str = field(default_factory=utc_timestamp)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SITE_PROFILE_SCHEMA_VERSION

    def to_dict(self):
        self.validate()
        payload = serialize(self)
        payload["summary"] = self.summary()
        return payload

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict):
            raise TypeError("SiteProfile payload must be a mapping")
        identity_payload = payload.get("site_identity") or {}
        identity = SiteIdentity(
            site_id=str(identity_payload.get("site_id") or ""),
            base_url=str(identity_payload.get("base_url") or ""),
            site_type=_enum_value(
                SiteType,
                identity_payload.get("site_type"),
                "site_identity.site_type",
            ),
            platform=_enum_value(
                SitePlatform,
                identity_payload.get("platform"),
                "site_identity.platform",
            ),
            locale=_optional_text(identity_payload.get("locale")),
            region=_optional_text(identity_payload.get("region")),
            metadata=_mapping(identity_payload.get("metadata")),
        )
        pages = []
        for index, value in enumerate(payload.get("pages") or []):
            if not isinstance(value, dict):
                raise TypeError(f"pages[{index}] must be a mapping")
            pages.append(
                ProfilePage(
                    page_id=str(value.get("page_id") or ""),
                    page_type=_enum_value(
                        PageType,
                        value.get("page_type"),
                        f"pages[{index}].page_type",
                    ),
                    url=str(value.get("url") or ""),
                    source=_enum_value(
                        ProfileSource,
                        value.get("source"),
                        f"pages[{index}].source",
                    ),
                    confidence=_confidence(
                        value.get("confidence"),
                        f"pages[{index}].confidence",
                    ),
                    enabled=_boolean(
                        value.get("enabled", True),
                        f"pages[{index}].enabled",
                    ),
                    representative=_boolean(
                        value.get("representative", True),
                        f"pages[{index}].representative",
                    ),
                    metadata=_mapping(value.get("metadata")),
                )
            )
        policy_payload = payload.get("interaction_policy") or {}
        allowed_levels = policy_payload.get("allowed_levels", ["SAFE"])
        if not isinstance(allowed_levels, list):
            raise TypeError("interaction_policy.allowed_levels must be a list")
        overrides = policy_payload.get("capability_overrides") or {}
        if not isinstance(overrides, dict):
            raise TypeError(
                "interaction_policy.capability_overrides must be a mapping"
            )
        interaction_policy = ProfileInteractionPolicy(
            allowed_levels=[
                _enum_value(
                    SideEffectLevel,
                    value,
                    "interaction_policy.allowed_levels",
                )
                for value in allowed_levels
            ],
            transactional_requires_explicit_opt_in=_boolean(
                policy_payload.get(
                    "transactional_requires_explicit_opt_in",
                    True,
                ),
                "interaction_policy.transactional_requires_explicit_opt_in",
            ),
            high_risk_allowed=_boolean(
                policy_payload.get("high_risk_allowed", False),
                "interaction_policy.high_risk_allowed",
            ),
            capability_overrides={
                canonical_capability_name(name): _enum_value(
                    SideEffectLevel,
                    level,
                    f"interaction_policy.capability_overrides.{name}",
                )
                for name, level in overrides.items()
            },
        )
        capabilities = []
        for index, value in enumerate(payload.get("capabilities") or []):
            if not isinstance(value, dict):
                raise TypeError(f"capabilities[{index}] must be a mapping")
            page_type_value = value.get("page_type")
            capabilities.append(
                ProfileCapability(
                    name=canonical_capability_name(value.get("name")),
                    scope=_enum_value(
                        CapabilityScope,
                        value.get("scope"),
                        f"capabilities[{index}].scope",
                    ),
                    status=_enum_value(
                        CapabilityStatus,
                        value.get("status"),
                        f"capabilities[{index}].status",
                    ),
                    source=_enum_value(
                        ProfileSource,
                        value.get("source"),
                        f"capabilities[{index}].source",
                    ),
                    confidence=_confidence(
                        value.get("confidence"),
                        f"capabilities[{index}].confidence",
                    ),
                    interaction_policy=_enum_value(
                        SideEffectLevel,
                        value.get("interaction_policy"),
                        f"capabilities[{index}].interaction_policy",
                    ),
                    default_interaction_allowed=_boolean(
                        value.get("default_interaction_allowed", False),
                        f"capabilities[{index}].default_interaction_allowed",
                    ),
                    interaction_reason=str(
                        value.get("interaction_reason") or "unknown"
                    ),
                    page_type=(
                        _enum_value(
                            PageType,
                            page_type_value,
                            f"capabilities[{index}].page_type",
                        )
                        if page_type_value is not None
                        else None
                    ),
                    page_id=_optional_text(value.get("page_id")),
                    selector_hint=value.get("selector_hint"),
                    metadata=_mapping(value.get("metadata")),
                )
            )
        profile = cls(
            profile_id=str(payload.get("profile_id") or ""),
            site_identity=identity,
            pages=pages,
            capabilities=capabilities,
            interaction_policy=interaction_policy,
            generated_at=str(payload.get("generated_at") or utc_timestamp()),
            metadata=_mapping(payload.get("metadata")),
            schema_version=str(
                payload.get("schema_version")
                or SITE_PROFILE_SCHEMA_VERSION
            ),
        )
        profile.validate()
        return profile

    def validate(self):
        if self.schema_version != SITE_PROFILE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported SiteProfile schema_version={self.schema_version!r}"
            )
        if not self.profile_id.strip():
            raise ValueError("profile_id must be a non-empty string")
        if not self.site_identity.site_id.strip():
            raise ValueError("site_identity.site_id must be a non-empty string")
        _validate_url(self.site_identity.base_url, "site_identity.base_url")
        page_ids = set()
        page_types = {}
        for page in self.pages:
            if not page.page_id.strip():
                raise ValueError("page_id must be a non-empty string")
            if page.page_id in page_ids:
                raise ValueError(f"Duplicate page_id: {page.page_id}")
            page_ids.add(page.page_id)
            page_types[page.page_id] = page.page_type
            _validate_url(page.url, f"pages.{page.page_id}.url")
            _confidence(page.confidence, f"pages.{page.page_id}.confidence")
        capability_keys = set()
        for capability in self.capabilities:
            if not capability.name:
                raise ValueError("Capability name must be non-empty")
            _confidence(
                capability.confidence,
                f"capabilities.{capability.name}.confidence",
            )
            key = (
                capability.scope.value,
                capability.page_id,
                capability.name,
            )
            if key in capability_keys:
                raise ValueError(
                    "Duplicate capability scope: "
                    f"{capability.scope.value}/{capability.page_id}/"
                    f"{capability.name}"
                )
            capability_keys.add(key)
            if capability.scope == CapabilityScope.PAGE:
                if not capability.page_id or capability.page_id not in page_ids:
                    raise ValueError(
                        f"Page capability {capability.name} has unknown page_id"
                    )
                if capability.page_type != page_types[capability.page_id]:
                    raise ValueError(
                        f"Page capability {capability.name} page_type mismatch"
                    )
            elif capability.page_id is not None:
                raise ValueError(
                    f"Site capability {capability.name} must not have page_id"
                )
        if not self.interaction_policy.allowed_levels:
            raise ValueError("interaction_policy.allowed_levels must not be empty")
        return self

    def page(self, page_id):
        return next(
            (page for page in self.pages if page.page_id == page_id),
            None,
        )

    def page_capabilities(self, page_id):
        return [
            capability
            for capability in self.capabilities
            if capability.scope == CapabilityScope.PAGE
            and capability.page_id == page_id
        ]

    def site_capabilities(self):
        return [
            capability
            for capability in self.capabilities
            if capability.scope == CapabilityScope.SITE
        ]

    def summary(self):
        status_counts = {
            status.value: sum(
                1
                for capability in self.capabilities
                if capability.status == status
            )
            for status in CapabilityStatus
        }
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "site_id": self.site_identity.site_id,
            "page_count": len(self.pages),
            "site_capability_count": len(self.site_capabilities()),
            "page_capability_count": sum(
                1
                for capability in self.capabilities
                if capability.scope == CapabilityScope.PAGE
            ),
            "capability_status_counts": status_counts,
        }


def _enum_value(enum_type, value, path):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value).strip().upper())
    except (TypeError, ValueError) as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{path} must be one of {allowed}") from error


def _confidence(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{path} must be a number")
    normalized = float(value)
    if not 0 <= normalized <= 1:
        raise ValueError(f"{path} must be between 0 and 1")
    return normalized


def _boolean(value, path):
    if not isinstance(value, bool):
        raise TypeError(f"{path} must be a boolean")
    return value


def _mapping(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("metadata must be a mapping")
    return dict(value)


def _optional_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_url(value, path):
    try:
        parsed = urlsplit(str(value))
    except ValueError as error:
        raise ValueError(f"{path} must be an absolute HTTP(S) URL") from error
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{path} must be an absolute HTTP(S) URL")
