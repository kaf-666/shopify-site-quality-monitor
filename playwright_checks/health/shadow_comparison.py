import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

from playwright_checks.health.execution_models import CheckResult, ExecutionStatus
from playwright_checks.health.models import HealthStatus, Severity, serialize
from playwright_checks.health.planner import TestPlan
from playwright_checks.health.shadow_maturity import ShadowMaturityPolicy
from playwright_checks.health.site_profile import CapabilityStatus


SHADOW_COMPARISON_SCHEMA_VERSION = "1.1"


class MappingMatch(str, Enum):
    EXACT = "EXACT"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MigrationReadiness(str, Enum):
    SHADOW = "SHADOW"
    ADVISORY = "ADVISORY"
    GATE_CANDIDATE = "GATE_CANDIDATE"
    PRIMARY = "PRIMARY"


class ShadowMaturityStage(str, Enum):
    SHADOW_NOT_READY = "SHADOW_NOT_READY"
    SHADOW_MATURING = "SHADOW_MATURING"
    ADVISORY_CANDIDATE = "ADVISORY_CANDIDATE"


@dataclass(frozen=True)
class LegacyCheckMapping:
    page_id: str
    result_type: str
    legacy_case: str
    check_id: str
    match: MappingMatch
    rationale: str


@dataclass
class ShadowComparison:
    run_id: str
    site_id: str
    legacy_check_count: int
    applicable_legacy_check_count: int
    planned_check_count: int
    mapped_legacy_check_count: int
    exact_mapping_count: int
    partial_mapping_count: int
    missing_in_new_count: int
    not_applicable_count: int
    executable_count: int
    executable_mapped_legacy_count: int
    unsupported_executor_count: int
    executor_error_count: int
    executor_timeout_count: int
    flaky_count: int
    result_parity_percent: float | None
    result_parity_sample_count: int
    evidence_parity_percent: float | None
    evidence_parity_sample_count: int
    overall_coverage_percent: float
    mapping_coverage_percent: float
    executable_coverage_percent: float
    critical_coverage_percent: float
    critical_executable_coverage_percent: float
    policy_regression_count: int
    mapping_fingerprint: str
    migration_status: MigrationReadiness = MigrationReadiness.SHADOW
    recommended_readiness: MigrationReadiness = MigrationReadiness.SHADOW
    maturity_stage: ShadowMaturityStage = ShadowMaturityStage.SHADOW_NOT_READY
    consecutive_stable_runs: int = 0
    stability: dict = field(default_factory=dict)
    readiness_targets: dict = field(default_factory=dict)
    mappings: list[dict] = field(default_factory=list)
    coverage_matrix: list[dict] = field(default_factory=list)
    missing_in_new: list[dict] = field(default_factory=list)
    not_applicable: list[dict] = field(default_factory=list)
    policy_regressions: list[dict] = field(default_factory=list)
    shadow_health_summary: dict = field(default_factory=dict)
    schema_version: str = SHADOW_COMPARISON_SCHEMA_VERSION

    def to_dict(self):
        payload = serialize(self)
        payload["migration_status_reason"] = (
            "Phase 3.5A is contractually shadow-only; recommendations require "
            "manual approval and cannot auto-promote runtime status."
        )
        return payload


class ShadowComparisonBuilder:
    def __init__(self, mappings=None, maturity_policy=None):
        self.mappings = tuple(
            DEFAULT_LEGACY_MAPPINGS if mappings is None else mappings
        )
        self.maturity_policy = maturity_policy or ShadowMaturityPolicy()
        self._validate_mappings()

    def build(
        self,
        run_id,
        site_id,
        legacy_results,
        plan,
        check_results,
        shadow_health_summary=None,
    ):
        if not isinstance(plan, TestPlan):
            raise TypeError("plan must be TestPlan")
        if any(not isinstance(value, CheckResult) for value in check_results):
            raise TypeError("check_results must contain CheckResult values")
        legacy = [
            dict(value)
            for value in (legacy_results or [])
            if isinstance(value, dict) and _is_legacy_check(value)
        ]
        plan_index = {
            (check.page_id, check.check_id): check for check in plan.checks
        }
        result_index = {
            (
                result.page_id,
                result.check_id,
                str(result.metadata.get("viewport") or "unknown"),
            ): result
            for result in check_results
        }
        mapping_index = {}
        for mapping in self.mappings:
            key = (mapping.page_id, mapping.result_type, mapping.legacy_case)
            mapping_index.setdefault(key, []).append(mapping)

        mapped_legacy = 0
        executable_mapped_legacy = 0
        exact = 0
        partial = 0
        missing = []
        not_applicable = []
        relationships = []
        coverage_matrix = []
        result_samples = []
        evidence_candidates = {}
        policy_regressions = []

        for legacy_result in legacy:
            identity = _legacy_identity(legacy_result)
            if str(legacy_result.get("status") or "").lower() == "skipped":
                item = {"legacy": identity, "reason": "legacy_check_skipped"}
                not_applicable.append(item)
                coverage_matrix.append(
                    _matrix_row(
                        identity,
                        mapping_status=MappingMatch.NOT_APPLICABLE,
                        gap="legacy_check_skipped",
                    )
                )
                continue

            key = (
                identity["page"],
                identity["result_type"],
                identity["case"],
            )
            configured = mapping_index.get(key, [])
            available = [
                mapping
                for mapping in configured
                if (mapping.page_id, mapping.check_id) in plan_index
            ]
            if not available:
                reason = (
                    "mapped_check_not_planned"
                    if configured
                    else "no_explicit_semantic_mapping"
                )
                item = {"legacy": identity, "reason": reason}
                missing.append(item)
                coverage_matrix.append(
                    _matrix_row(
                        identity,
                        mapping_status=MappingMatch.MISSING,
                        gap=reason,
                    )
                )
                continue

            if all(
                plan_index[(mapping.page_id, mapping.check_id)].capability_status
                in (CapabilityStatus.UNKNOWN, CapabilityStatus.NOT_APPLICABLE)
                for mapping in available
            ):
                item = {
                    "legacy": identity,
                    "reason": "no_configured_capability_signal",
                }
                not_applicable.append(item)
                for mapping in available:
                    planned = plan_index[(mapping.page_id, mapping.check_id)]
                    new_result = result_index.get(
                        (mapping.page_id, mapping.check_id, identity["viewport"])
                    )
                    coverage_matrix.append(
                        _matrix_row(
                            identity,
                            capability=planned.capability,
                            planned_check=mapping.check_id,
                            executor=planned.executor,
                            mapping_status=MappingMatch.NOT_APPLICABLE,
                            execution_status=(
                                new_result.execution_status.value
                                if new_result
                                else "NOT_RECORDED"
                            ),
                            gap="no_configured_capability_signal",
                        )
                    )
                continue

            mapped_legacy += 1
            viewport = identity["viewport"]
            relation_execution = []
            for mapping in available:
                if mapping.match == MappingMatch.EXACT:
                    exact += 1
                else:
                    partial += 1
                planned = plan_index[(mapping.page_id, mapping.check_id)]
                new_result = result_index.get(
                    (mapping.page_id, mapping.check_id, viewport)
                )
                execution_status = (
                    new_result.execution_status.value
                    if new_result
                    else "NOT_RECORDED"
                )
                executable = bool(
                    new_result
                    and new_result.execution_status == ExecutionStatus.COMPLETED
                )
                relation_execution.append(executable)
                gap = _execution_gap(new_result)
                if gap is None and mapping.match == MappingMatch.PARTIAL:
                    gap = "partial_semantic_coverage_only"
                relation = {
                    "legacy": identity,
                    "capability": planned.capability,
                    "planned_check": mapping.check_id,
                    "check_id": mapping.check_id,
                    "executor": planned.executor,
                    "mapping_status": mapping.match.value,
                    "match": mapping.match.value,
                    "execution_status": execution_status,
                    "new_execution_status": execution_status,
                    "executable": executable,
                    "gap": gap,
                    "rationale": mapping.rationale,
                    "new_health_status": (
                        new_result.health_status.value
                        if new_result
                        else HealthStatus.UNVERIFIED.value
                    ),
                    "result_parity": None,
                    "parity_eligible": bool(
                        mapping.result_type == "deterministic_check"
                        or mapping.match == MappingMatch.EXACT
                    ),
                }
                legacy_health = _legacy_health_status(
                    legacy_result.get("status")
                )
                if (
                    executable
                    and legacy_health is not None
                    and relation["parity_eligible"]
                ):
                    parity = legacy_health == new_result.health_status
                    result_samples.append(parity)
                    relation["result_parity"] = parity
                    evidence_candidates[new_result.result_id] = (
                        new_result,
                        planned,
                    )
                if (
                    new_result
                    and new_result.execution_status
                    == ExecutionStatus.POLICY_BLOCKED
                    and legacy_health is not None
                ):
                    policy_regressions.append(
                        {
                            "legacy": identity,
                            "check_id": mapping.check_id,
                            "reason": new_result.metadata.get("reason"),
                        }
                    )
                relationships.append(relation)
                coverage_matrix.append(dict(relation))
            if relation_execution and all(relation_execution):
                executable_mapped_legacy += 1

        evidence_samples = []
        for result, planned in evidence_candidates.values():
            actual_types = {item.evidence_type for item in result.evidence}
            required = set(planned.evidence_requirements)
            evidence_samples.append(required.issubset(actual_types))

        applicable_count = len(legacy) - len(not_applicable)
        coverage = _percent(mapped_legacy, applicable_count, empty=100.0)
        executable_coverage = _percent(
            executable_mapped_legacy,
            applicable_count,
            empty=100.0,
        )
        critical = [
            check
            for check in plan.checks
            if check.severity in (Severity.HIGH, Severity.CRITICAL)
            and check.capability_status == CapabilityStatus.PRESENT
        ]
        mapped_keys = {
            (value["legacy"]["page"], value["check_id"])
            for value in relationships
        }
        completed_keys = {
            (result.page_id, result.check_id)
            for result in check_results
            if result.execution_status == ExecutionStatus.COMPLETED
        }
        critical_coverage = _percent(
            sum(
                1
                for check in critical
                if (check.page_id, check.check_id) in mapped_keys
            ),
            len(critical),
            empty=100.0,
        )
        critical_executable_coverage = _percent(
            sum(
                1
                for check in critical
                if (check.page_id, check.check_id) in completed_keys
            ),
            len(critical),
            empty=100.0,
        )
        result_parity = _optional_percent(result_samples)
        evidence_parity = _optional_percent(evidence_samples)
        comparison = ShadowComparison(
            run_id=str(run_id),
            site_id=str(site_id),
            legacy_check_count=len(legacy),
            applicable_legacy_check_count=applicable_count,
            planned_check_count=len(plan.checks),
            mapped_legacy_check_count=mapped_legacy,
            exact_mapping_count=exact,
            partial_mapping_count=partial,
            missing_in_new_count=len(missing),
            not_applicable_count=len(not_applicable),
            executable_count=sum(
                1
                for result in check_results
                if result.execution_status == ExecutionStatus.COMPLETED
            ),
            executable_mapped_legacy_count=executable_mapped_legacy,
            unsupported_executor_count=sum(
                1
                for result in check_results
                if result.execution_status == ExecutionStatus.UNSUPPORTED
            ),
            executor_error_count=sum(
                1
                for result in check_results
                if result.execution_status == ExecutionStatus.ERROR
            ),
            executor_timeout_count=sum(
                1
                for result in check_results
                if result.execution_status == ExecutionStatus.TIMEOUT
            ),
            flaky_count=sum(
                1
                for result in check_results
                if result.execution_status == ExecutionStatus.COMPLETED
                and result.retry_count > 0
            ),
            result_parity_percent=result_parity,
            result_parity_sample_count=len(result_samples),
            evidence_parity_percent=evidence_parity,
            evidence_parity_sample_count=len(evidence_samples),
            overall_coverage_percent=coverage,
            mapping_coverage_percent=coverage,
            executable_coverage_percent=executable_coverage,
            critical_coverage_percent=critical_coverage,
            critical_executable_coverage_percent=critical_executable_coverage,
            policy_regression_count=len(policy_regressions),
            mapping_fingerprint=_mapping_fingerprint(plan, self.mappings),
            mappings=relationships,
            coverage_matrix=coverage_matrix,
            missing_in_new=missing,
            not_applicable=not_applicable,
            policy_regressions=policy_regressions,
            shadow_health_summary=dict(shadow_health_summary or {}),
            readiness_targets=self.maturity_policy.to_dict(),
        )
        apply_maturity(comparison, self.maturity_policy)
        return comparison

    def _validate_mappings(self):
        selected = set()
        for index, mapping in enumerate(self.mappings):
            if not isinstance(mapping, LegacyCheckMapping):
                raise TypeError(
                    f"Legacy mapping {index} must be LegacyCheckMapping"
                )
            if mapping.match not in (MappingMatch.EXACT, MappingMatch.PARTIAL):
                raise ValueError("Static legacy mappings must be EXACT or PARTIAL")
            key = (
                mapping.page_id,
                mapping.result_type,
                mapping.legacy_case,
                mapping.check_id,
            )
            if key in selected:
                raise ValueError(f"Duplicate legacy mapping: {key}")
            selected.add(key)


def apply_maturity(comparison, policy, stability=None):
    if not isinstance(comparison, ShadowComparison):
        raise TypeError("comparison must be ShadowComparison")
    if not isinstance(policy, ShadowMaturityPolicy):
        raise TypeError("policy must be ShadowMaturityPolicy")
    if stability is not None:
        comparison.stability = dict(stability)
        comparison.consecutive_stable_runs = int(
            stability.get("consecutive_stable_runs", 0) or 0
        )
    record = {
        "overall_coverage": comparison.overall_coverage_percent,
        "critical_coverage": comparison.critical_coverage_percent,
        "executable_coverage": comparison.executable_coverage_percent,
        "result_parity": comparison.result_parity_percent,
        "evidence_parity": comparison.evidence_parity_percent,
        "policy_regressions": comparison.policy_regression_count,
        "executor_errors": comparison.executor_error_count,
        "executor_timeouts": comparison.executor_timeout_count,
    }
    current_stable = policy.stable_record(record, mapping_consistent=True)
    if stability is not None and stability.get("last_5_runs"):
        current_stable = bool(stability["last_5_runs"][-1].get("stable"))
    if not current_stable:
        comparison.maturity_stage = ShadowMaturityStage.SHADOW_NOT_READY
        comparison.recommended_readiness = MigrationReadiness.SHADOW
    elif (
        comparison.consecutive_stable_runs
        >= policy.required_consecutive_stable_runs
    ):
        comparison.maturity_stage = ShadowMaturityStage.ADVISORY_CANDIDATE
        comparison.recommended_readiness = MigrationReadiness.ADVISORY
    else:
        comparison.maturity_stage = ShadowMaturityStage.SHADOW_MATURING
        comparison.recommended_readiness = MigrationReadiness.SHADOW
    comparison.migration_status = MigrationReadiness.SHADOW
    return comparison


def _matrix_row(
    legacy,
    capability=None,
    planned_check=None,
    executor=None,
    mapping_status=MappingMatch.MISSING,
    execution_status="NOT_RECORDED",
    executable=False,
    gap=None,
):
    return {
        "legacy": legacy,
        "capability": capability,
        "planned_check": planned_check,
        "check_id": planned_check,
        "executor": executor,
        "mapping_status": mapping_status.value,
        "match": mapping_status.value,
        "execution_status": execution_status,
        "new_execution_status": execution_status,
        "executable": bool(executable),
        "gap": gap,
        "result_parity": None,
    }


def _execution_gap(result):
    if result is None:
        return "new_result_not_recorded"
    return {
        ExecutionStatus.UNSUPPORTED: "unsupported_executor",
        ExecutionStatus.POLICY_BLOCKED: "interaction_policy_blocked",
        ExecutionStatus.ERROR: "executor_error",
        ExecutionStatus.TIMEOUT: "executor_timeout",
        ExecutionStatus.SKIPPED: "planned_check_skipped",
    }.get(result.execution_status)


def _mapping(page, result_type, legacy_case, check_id, match, rationale):
    return LegacyCheckMapping(
        page_id=page,
        result_type=result_type,
        legacy_case=legacy_case,
        check_id=check_id,
        match=match,
        rationale=rationale,
    )


def _maps(page, result_type, legacy_case, entries):
    return tuple(
        _mapping(page, result_type, legacy_case, check_id, match, rationale)
        for check_id, match, rationale in entries
    )


DEFAULT_LEGACY_MAPPINGS = (
    *_maps(
        "home",
        "deterministic_check",
        "dom_modules",
        (
            (
                "home.core_modules.health",
                MappingMatch.EXACT,
                "Both require every configured Home module to be visible with layout.",
            ),
            (
                "home.navigation.presence",
                MappingMatch.PARTIAL,
                "The legacy aggregate includes configured header navigation modules.",
            ),
            (
                "home.navigation.visible",
                MappingMatch.PARTIAL,
                "The legacy aggregate includes visible header navigation modules.",
            ),
            (
                "global.navigation.health",
                MappingMatch.PARTIAL,
                "Legacy module measurement proves the page rendered after navigation but does not preserve HTTP status.",
            ),
        ),
    ),
    _mapping(
        "home",
        "deterministic_check",
        "dom_presence",
        "home.presence_signals.health",
        MappingMatch.EXACT,
        "Both require every configured Home presence signal to be attached.",
    ),
    _mapping(
        "home",
        "deterministic_check",
        "plugins",
        "home.plugin_signals.health",
        MappingMatch.PARTIAL,
        "Both check configured plugins; legacy adds currency text semantics.",
    ),
    _mapping(
        "home", "visual", "banner", "home.hero.health", MappingMatch.PARTIAL,
        "Semantic hero visibility does not claim pixel equivalence.",
    ),
    _mapping(
        "home", "visual", "collections", "home.core_modules.health", MappingMatch.PARTIAL,
        "Core module health covers structure but not collection pixels.",
    ),
    _mapping(
        "home", "visual", "collections_1", "home.core_modules.health", MappingMatch.PARTIAL,
        "Core module health covers structure but not collection pixels.",
    ),
    *_maps(
        "home", "visual", "home_first_screen",
        (
            ("home.main_content.health", MappingMatch.PARTIAL, "Checks non-empty main content, not screenshot pixels."),
            ("home.core_modules.health", MappingMatch.PARTIAL, "Checks configured first-screen modules semantically."),
        ),
    ),
    *_maps(
        "home", "visual", "home_full_page",
        (
            ("home.main_content.health", MappingMatch.PARTIAL, "Checks non-empty main content, not screenshot pixels."),
            ("home.core_modules.health", MappingMatch.PARTIAL, "Checks configured page modules semantically."),
        ),
    ),
    _mapping(
        "home", "visual", "mobile_menu_open",
        "home.mobile_navigation.control_presence", MappingMatch.PARTIAL,
        "Checks the configured trigger only; it does not reproduce open-panel pixels.",
    ),
    _mapping(
        "home", "visual", "wishlist", "home.plugin_signals.health", MappingMatch.PARTIAL,
        "Checks plugin DOM health; existing visual engine remains authoritative for pixels.",
    ),
    _mapping(
        "home", "visual", "currency", "home.plugin_signals.health", MappingMatch.PARTIAL,
        "Checks plugin DOM health; existing visual engine remains authoritative for pixels.",
    ),
    *_maps(
        "collection", "deterministic_check", "dom_modules",
        (
            ("plp.core_modules.health", MappingMatch.EXACT, "Both require every configured PLP module to be visible with layout."),
            ("global.navigation.health", MappingMatch.PARTIAL, "Legacy module measurement proves the page rendered after navigation but does not preserve HTTP status."),
        ),
    ),
    _mapping(
        "collection", "deterministic_check", "dom_presence",
        "plp.presence_signals.health", MappingMatch.EXACT,
        "Both require every configured PLP presence signal to be attached.",
    ),
    *_maps(
        "collection", "deterministic_check", "product_count",
        (
            ("plp.product_grid.health", MappingMatch.PARTIAL, "Both enforce a non-empty product count; legacy may scroll and compare a reference count."),
            ("plp.product_card.presence", MappingMatch.PARTIAL, "Product-card presence is a semantic subset of legacy count health."),
        ),
    ),
    _mapping(
        "collection", "visual", "product_count",
        "plp.product_grid.health", MappingMatch.PARTIAL,
        "Legacy records reference-count content changes; new check enforces current non-empty count health.",
    ),
    _mapping(
        "collection", "deterministic_check", "pagination",
        "plp.pagination.health", MappingMatch.PARTIAL,
        "New check confirms presence; legacy also validates links and overflow.",
    ),
    *_maps(
        "collection", "visual", "product_grid",
        (
            ("plp.product_grid.visible", MappingMatch.PARTIAL, "Semantic visibility does not claim pixel equivalence."),
            ("plp.product_card.title_presence", MappingMatch.PARTIAL, "Samples card title structure, not pixels."),
            ("plp.product_card.price_presence", MappingMatch.PARTIAL, "Samples card price structure, not pixels."),
            ("plp.product_card.image_presence", MappingMatch.PARTIAL, "Samples card image structure, not pixels."),
        ),
    ),
    *_maps(
        "collection", "visual", "collection_first_screen",
        (
            ("plp.core_modules.health", MappingMatch.PARTIAL, "Checks configured core structure, not pixels."),
            ("plp.product_grid.visible", MappingMatch.PARTIAL, "Checks visible product grid, not pixels."),
        ),
    ),
    *_maps(
        "collection", "visual", "collection_full_page",
        (
            ("plp.core_modules.health", MappingMatch.PARTIAL, "Checks configured core structure, not pixels."),
            ("plp.product_grid.visible", MappingMatch.PARTIAL, "Checks visible product grid, not pixels."),
        ),
    ),
    _mapping(
        "collection", "visual", "filter_open", "plp.filter.health", MappingMatch.PARTIAL,
        "Checks filter control visibility; legacy also opens and captures the panel.",
    ),
    _mapping(
        "collection", "visual", "filter_drawer_open", "plp.filter.health", MappingMatch.PARTIAL,
        "Checks filter control visibility; legacy also opens and captures the drawer.",
    ),
    *_maps(
        "product", "deterministic_check", "dom_modules",
        (
            ("pdp.core_modules.health", MappingMatch.EXACT, "Both require every configured PDP module to be visible with layout."),
            ("global.navigation.health", MappingMatch.PARTIAL, "Legacy module measurement proves the page rendered after navigation but does not preserve HTTP status."),
        ),
    ),
    _mapping(
        "product", "deterministic_check", "dom_presence",
        "pdp.presence_signals.health", MappingMatch.EXACT,
        "Both require every configured PDP presence signal to be attached.",
    ),
    *_maps(
        "product", "deterministic_check", "product_content",
        (
            ("pdp.product_title.presence", MappingMatch.PARTIAL, "Legacy combines title and price; this isolates title text."),
            ("pdp.product_price.presence", MappingMatch.PARTIAL, "Legacy combines title and price; this isolates price text."),
        ),
    ),
    _mapping(
        "product", "deterministic_check", "add_to_cart_state",
        "commerce.add_to_cart.control_health", MappingMatch.PARTIAL,
        "Both are read-only control observations; legacy additionally selects the best viewport-ready candidate.",
    ),
    _mapping(
        "product", "deterministic_check", "variant_selection",
        "pdp.variant_selector.health", MappingMatch.PARTIAL,
        "New check confirms the configured control; legacy may select a variant and compare changed state.",
    ),
    *_maps(
        "product", "visual", "gallery",
        (
            ("pdp.gallery.health", MappingMatch.PARTIAL, "Gallery visibility is semantic, not pixel equivalence."),
            ("pdp.product_main_image.health", MappingMatch.PARTIAL, "Checks a visible image inside the gallery."),
        ),
    ),
    *_maps(
        "product", "visual", "info",
        (
            ("pdp.product_info.health", MappingMatch.PARTIAL, "Checks product information container visibility."),
            ("pdp.product_title.presence", MappingMatch.PARTIAL, "Checks title text inside the visual region semantics."),
            ("pdp.product_price.presence", MappingMatch.PARTIAL, "Checks price text inside the visual region semantics."),
            ("commerce.add_to_cart.control_health", MappingMatch.PARTIAL, "Checks CTA control health without a click."),
        ),
    ),
    *_maps(
        "product", "visual", "product_main",
        (
            ("pdp.core_modules.health", MappingMatch.PARTIAL, "Checks configured PDP core modules."),
            ("pdp.product_info.health", MappingMatch.PARTIAL, "Checks product information visibility."),
            ("pdp.product_main_image.health", MappingMatch.PARTIAL, "Checks a visible main product image."),
            ("pdp.product_form.health", MappingMatch.PARTIAL, "Checks the cart form is attached."),
            ("commerce.add_to_cart.control_health", MappingMatch.PARTIAL, "Checks CTA control state without a click."),
        ),
    ),
    *_maps(
        "product", "visual", "product_first_screen",
        (
            ("pdp.core_modules.health", MappingMatch.PARTIAL, "Checks configured PDP structure, not pixels."),
            ("pdp.product_info.health", MappingMatch.PARTIAL, "Checks visible product information."),
            ("pdp.product_main_image.health", MappingMatch.PARTIAL, "Checks a visible main image."),
        ),
    ),
    *_maps(
        "product", "visual", "product_full_page",
        (
            ("pdp.core_modules.health", MappingMatch.PARTIAL, "Checks configured PDP structure, not pixels."),
            ("pdp.product_info.health", MappingMatch.PARTIAL, "Checks visible product information."),
            ("pdp.product_main_image.health", MappingMatch.PARTIAL, "Checks a visible main image."),
        ),
    ),
    _mapping(
        "product", "visual", "variant_changed_state",
        "pdp.variant_selector.health", MappingMatch.PARTIAL,
        "Checks variant control presence; existing visual engine owns changed-state pixels.",
    ),
    _mapping(
        "product", "visual", "sticky_add_to_cart",
        "commerce.add_to_cart.control_health", MappingMatch.PARTIAL,
        "Checks Add to Cart control health, not sticky-region pixels.",
    ),
)


def _is_legacy_check(result):
    result_type = str(result.get("result_type") or "visual")
    if result_type not in ("visual", "deterministic_check"):
        return False
    return str(result.get("case") or "") != "runtime"


def _legacy_identity(result):
    return {
        "result_type": str(result.get("result_type") or "visual"),
        "viewport": str(result.get("viewport") or "unknown"),
        "page": str(result.get("page") or "unknown"),
        "case": str(result.get("case") or "unknown"),
        "status": str(result.get("status") or "unknown"),
    }


def _legacy_health_status(value):
    return {
        "passed": HealthStatus.PASS,
        "initialized": HealthStatus.PASS,
        "failed": HealthStatus.FAIL,
        "warning": HealthStatus.WARN,
        "content_changed": HealthStatus.EXPECTED_CHANGE,
    }.get(str(value or "").lower())


def _mapping_fingerprint(plan, mappings):
    planned_pages = {check.page_id for check in plan.checks}
    payload = {
        "mappings": [
            {
                "page": value.page_id,
                "result_type": value.result_type,
                "legacy_case": value.legacy_case,
                "check_id": value.check_id,
                "match": value.match.value,
            }
            for value in mappings
            if value.page_id in planned_pages
        ],
        "plan": [
            {
                "page": check.page_id,
                "check_id": check.check_id,
                "capability": check.capability,
                "executor": check.executor,
                "capability_status": check.capability_status.value,
            }
            for check in plan.checks
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _percent(numerator, denominator, empty=0.0):
    if not denominator:
        return float(empty)
    return round(float(numerator) * 100 / float(denominator), 2)


def _optional_percent(samples):
    if not samples:
        return None
    return _percent(sum(1 for value in samples if value), len(samples))
