import time
from dataclasses import replace
from time import perf_counter

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from playwright_checks.health.execution_models import (
    CheckResult,
    ExecutionStatus,
    ExecutorContext,
    RuntimeMode,
    check_result_id,
)
from playwright_checks.health.executor_registry import ExecutorRegistry
from playwright_checks.health.executors import ExecutorInputError
from playwright_checks.health.models import HealthStatus, SideEffectLevel, utc_timestamp
from playwright_checks.health.planner import PlanDisposition
from playwright_checks.runtime.evidence import redact_text


class ExecutorEngine:
    """Resolve and execute one PlannedCheck without changing the legacy gate."""

    def __init__(self, registry=None):
        self.registry = registry or ExecutorRegistry()

    def execute(self, context):
        if not isinstance(context, ExecutorContext):
            raise TypeError("context must be ExecutorContext")
        context.validate()
        non_executed = self._preflight(context)
        if non_executed is not None:
            return non_executed

        definition = self.registry.resolve(context.planned_check.executor)
        if definition is None:
            return terminal_result(
                context,
                ExecutionStatus.UNSUPPORTED,
                HealthStatus.UNVERIFIED,
                executor_version="unresolved",
                reason="executor_key_not_registered",
                error={
                    "code": "unsupported_executor",
                    "message": (
                        f"No executor registered for "
                        f"{context.planned_check.executor}"
                    ),
                },
            )

        started_at = utc_timestamp()
        started = perf_counter()
        retries = 0
        while True:
            try:
                result = definition.executor(context)
                self._validate_executor_result(result, context, definition)
                return replace(
                    result,
                    started_at=started_at,
                    duration_ms=round((perf_counter() - started) * 1000, 3),
                    retry_count=retries,
                ).validate()
            except (TimeoutError, PlaywrightTimeoutError) as error:
                if (
                    retries < context.retry_policy.max_retries
                    and context.retry_policy.retry_on_timeout
                ):
                    retries += 1
                    self._retry_delay(context)
                    continue
                return terminal_result(
                    context,
                    ExecutionStatus.TIMEOUT,
                    HealthStatus.UNVERIFIED,
                    executor_version=definition.version,
                    reason="executor_timeout",
                    started_at=started_at,
                    duration_ms=(perf_counter() - started) * 1000,
                    retry_count=retries,
                    error={
                        "code": "timeout",
                        "message": redact_text(
                            f"{type(error).__name__}: {error}"
                        ),
                    },
                )
            except Exception as error:
                if (
                    retries < context.retry_policy.max_retries
                    and context.retry_policy.retry_on_error
                ):
                    retries += 1
                    self._retry_delay(context)
                    continue
                code = (
                    "executor_input_error"
                    if isinstance(error, ExecutorInputError)
                    else "executor_error"
                )
                return terminal_result(
                    context,
                    ExecutionStatus.ERROR,
                    HealthStatus.UNVERIFIED,
                    executor_version=definition.version,
                    reason=code,
                    started_at=started_at,
                    duration_ms=(perf_counter() - started) * 1000,
                    retry_count=retries,
                    error={
                        "code": code,
                        "message": redact_text(
                            f"{type(error).__name__}: {error}"
                        ),
                    },
                )

    def _preflight(self, context):
        check = context.planned_check
        if context.runtime_policy.mode != RuntimeMode.MONITOR:
            return terminal_result(
                context,
                ExecutionStatus.UNSUPPORTED,
                HealthStatus.UNVERIFIED,
                executor_version="reserved-mode",
                reason=f"runtime_mode_{context.runtime_policy.mode.value.lower()}_reserved",
            )
        if check.disposition == PlanDisposition.NOT_APPLICABLE:
            return terminal_result(
                context,
                ExecutionStatus.SKIPPED,
                HealthStatus.NOT_APPLICABLE,
                executor_version="not-executed",
                reason="capability_not_applicable",
            )
        if check.disposition == PlanDisposition.POLICY_BLOCKED:
            return terminal_result(
                context,
                ExecutionStatus.POLICY_BLOCKED,
                HealthStatus.UNVERIFIED,
                executor_version="not-executed",
                reason=check.reason,
            )
        if check.disposition in (
            PlanDisposition.CAPABILITY_UNKNOWN,
            PlanDisposition.DISABLED,
        ):
            return terminal_result(
                context,
                ExecutionStatus.SKIPPED,
                HealthStatus.UNVERIFIED,
                executor_version="not-executed",
                reason=check.reason,
            )
        if not check.interaction_allowed:
            return terminal_result(
                context,
                ExecutionStatus.POLICY_BLOCKED,
                HealthStatus.UNVERIFIED,
                executor_version="not-executed",
                reason="planned_interaction_not_allowed",
            )
        if check.interaction_policy == SideEffectLevel.HIGH_RISK:
            return terminal_result(
                context,
                ExecutionStatus.POLICY_BLOCKED,
                HealthStatus.UNVERIFIED,
                executor_version="not-executed",
                reason="monitor_high_risk_permanent_deny",
            )
        if (
            check.interaction_policy == SideEffectLevel.TRANSACTIONAL_SAFE
            and not context.runtime_policy.transactional_safe_enabled
        ):
            return terminal_result(
                context,
                ExecutionStatus.POLICY_BLOCKED,
                HealthStatus.UNVERIFIED,
                executor_version="not-executed",
                reason="monitor_transactional_safe_disabled",
            )
        if not check.should_execute:
            return terminal_result(
                context,
                ExecutionStatus.SKIPPED,
                HealthStatus.UNVERIFIED,
                executor_version="not-executed",
                reason=check.reason,
            )
        return None

    @staticmethod
    def _validate_executor_result(result, context, definition):
        if not isinstance(result, CheckResult):
            raise TypeError("Executor must return CheckResult")
        result.validate()
        if result.check_id != context.planned_check.check_id:
            raise ValueError("Executor returned a different check_id")
        if result.executor_key != definition.executor_key:
            raise ValueError("Executor returned a different executor_key")
        if result.executor_version != definition.version:
            raise ValueError("Executor returned a different executor_version")

    @staticmethod
    def _retry_delay(context):
        delay = context.retry_policy.retry_delay_ms / 1000
        if delay > 0:
            time.sleep(delay)


def terminal_result(
    context,
    execution_status,
    health_status,
    executor_version,
    reason,
    started_at=None,
    duration_ms=0,
    retry_count=0,
    error=None,
):
    result = CheckResult(
        result_id=check_result_id(context, context.planned_check.executor),
        check_id=context.planned_check.check_id,
        site_id=context.site_profile.site_identity.site_id,
        page_id=context.page_profile.page_id,
        page_type=context.page_profile.page_type,
        page_url=context.page_profile.url,
        capability=context.planned_check.capability,
        executor_key=context.planned_check.executor,
        executor_version=str(executor_version),
        execution_status=execution_status,
        health_status=health_status,
        expected=None,
        actual=None,
        observations=[{"kind": "execution_state", "reason": str(reason)}],
        evidence=[],
        started_at=started_at or utc_timestamp(),
        duration_ms=round(float(duration_ms), 3),
        retry_count=int(retry_count),
        error=error,
        metadata={
            "run_id": context.run_id,
            "shadow_only": context.runtime_policy.shadow_only,
            "runtime_mode": context.runtime_policy.mode.value,
            "viewport": context.metadata.get("viewport", "unknown"),
            "target": context.target,
            "reason": str(reason),
        },
    )
    return result.validate()
