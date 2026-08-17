from dataclasses import dataclass

from playwright_checks.health.capabilities import (
    CAPABILITY_RISKS,
    PROTECTED_HIGH_RISK_ACTIONS,
    canonical_capability_name,
)
from playwright_checks.health.models import SideEffectLevel


@dataclass(frozen=True)
class InteractionDecision:
    action: str
    level: SideEffectLevel
    allowed: bool
    reason: str
    explicit_opt_in: bool


class InteractionPolicy:
    """Central side-effect decision used by planners and explicit flows."""

    def __init__(self, config=None):
        self.config = dict(config or {})
        configured_levels = self.config.get("allowed_levels", ["SAFE"])
        self.allowed_levels = {
            SideEffectLevel(str(value).upper())
            for value in configured_levels
        }
        self.transactional_requires_opt_in = bool(
            self.config.get("transactional_requires_explicit_opt_in", True)
        )
        self.high_risk_allowed = bool(
            self.config.get("high_risk_allowed", False)
        )
        configured_overrides = self.config.get("capability_overrides", {}) or {}
        if not isinstance(configured_overrides, dict):
            raise TypeError("interaction_policy.capability_overrides must be a mapping")
        self.capability_overrides = {
            canonical_capability_name(name): SideEffectLevel(
                str(value).upper()
            )
            for name, value in configured_overrides.items()
        }

    def level_for(self, action, default_level=None):
        normalized = canonical_capability_name(action)
        inherent_level = CAPABILITY_RISKS.get(normalized)
        supplied_level = (
            default_level
            if isinstance(default_level, SideEffectLevel)
            else SideEffectLevel(str(default_level).upper())
            if default_level is not None
            else None
        )
        # A policy override may tighten a risk classification, but it may
        # never downgrade a capability whose contract is HIGH_RISK.
        if (
            normalized in PROTECTED_HIGH_RISK_ACTIONS
            or inherent_level == SideEffectLevel.HIGH_RISK
            or supplied_level == SideEffectLevel.HIGH_RISK
        ):
            return SideEffectLevel.HIGH_RISK
        if normalized in self.capability_overrides:
            return self.capability_overrides[normalized]
        if supplied_level is not None:
            return supplied_level
        return inherent_level or SideEffectLevel.HIGH_RISK

    def decide(self, action, explicit_opt_in=False, level=None):
        normalized = canonical_capability_name(action)
        level = self.level_for(normalized, default_level=level)
        explicit = bool(explicit_opt_in)

        if level == SideEffectLevel.HIGH_RISK:
            allowed = bool(self.high_risk_allowed and explicit)
            reason = (
                "high_risk_explicitly_approved"
                if allowed
                else "high_risk_default_deny"
            )
        elif level == SideEffectLevel.TRANSACTIONAL_SAFE:
            allowed_by_level = level in self.allowed_levels
            if self.transactional_requires_opt_in:
                allowed = explicit
            else:
                allowed = allowed_by_level or explicit
            reason = (
                "transactional_explicit_opt_in"
                if allowed
                else "transactional_opt_in_required"
            )
        else:
            allowed = level in self.allowed_levels
            reason = "safe_policy_allowed" if allowed else "safe_level_disabled"

        return InteractionDecision(
            action=normalized,
            level=level,
            allowed=allowed,
            reason=reason,
            explicit_opt_in=explicit,
        )
