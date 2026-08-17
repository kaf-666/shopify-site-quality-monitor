from playwright_checks.health.execution_models import CheckResult, ExecutionStatus
from playwright_checks.health.models import (
    EVIDENCE_RANK,
    EvidenceLevel,
    HealthDimension,
    HealthStatus,
)
from playwright_checks.runtime.evidence import sanitize_payload


OBSERVATION_ADAPTER_SCHEMA_VERSION = "1.0"


class CheckResultObservationAdapter:
    """Translate the new result contract into the existing observation stream."""

    schema_version = OBSERVATION_ADAPTER_SCHEMA_VERSION

    def adapt(self, result, viewport=None):
        if not isinstance(result, CheckResult):
            raise TypeError("result must be CheckResult")
        result.validate()
        status = _legacy_status(result)
        messages = []
        if result.error:
            messages.append(str(result.error.get("message") or result.error))
        evidence_level = max(
            (item.level for item in result.evidence),
            key=lambda value: EVIDENCE_RANK[value],
            default=EvidenceLevel.NONE,
        )
        payload = {
            "schema_version": self.schema_version,
            "result_type": "deterministic_check",
            "site": result.site_id,
            "suite": "shadow_executor",
            "run_id": result.metadata.get("run_id") or _run_id(result.result_id),
            "viewport": (
                viewport
                or result.metadata.get("viewport")
                or "unknown"
            ),
            "page": result.page_id,
            "page_type": result.page_type.value,
            "page_url": result.page_url,
            "case": result.check_id,
            "check_id": result.check_id,
            "dimension": _dimension(result.capability).value,
            "capability": result.capability,
            "executor_key": result.executor_key,
            "executor_version": result.executor_version,
            "execution_status": result.execution_status.value,
            "health_status": result.health_status.value,
            "status": status,
            "affects_exit_code": False,
            "shadow": True,
            "messages": messages,
            "evidence_level": evidence_level.value,
            "evidence": [item for item in result.to_dict()["evidence"]],
            "expected": result.expected,
            "actual": result.actual,
            "details": {
                "result_id": result.result_id,
                "duration_ms": result.duration_ms,
                "retry_count": result.retry_count,
                "observations": list(result.observations),
                "metadata": dict(result.metadata),
            },
        }
        return sanitize_payload(payload)

    def adapt_many(self, results):
        return [self.adapt(result) for result in results]


def _legacy_status(result):
    if result.execution_status != ExecutionStatus.COMPLETED:
        return "skipped"
    return {
        HealthStatus.PASS: "passed",
        HealthStatus.WARN: "warning",
        HealthStatus.FAIL: "failed",
        HealthStatus.FLAKY: "warning",
        HealthStatus.EXPECTED_CHANGE: "content_changed",
        HealthStatus.NOT_APPLICABLE: "skipped",
        HealthStatus.BLOCKED: "skipped",
        HealthStatus.UNVERIFIED: "skipped",
    }.get(result.health_status, "skipped")


def _dimension(capability):
    normalized = str(capability or "").lower()
    if normalized in {
        "navigation",
    }:
        return HealthDimension.AVAILABILITY
    if normalized in {
        "add_to_cart",
        "buy_now",
        "cart_drawer",
        "cart_page",
        "cart_quantity",
        "cart_remove",
    }:
        return HealthDimension.COMMERCE
    if normalized in {
        "filter",
        "sort",
        "pagination",
        "load_more",
        "variant_selector",
        "size_selector",
        "color_selector",
    }:
        return HealthDimension.FUNCTIONAL
    return HealthDimension.DOM_CONTENT


def _run_id(result_id):
    return "shadow-" + str(result_id).rsplit("-", 1)[-1]
