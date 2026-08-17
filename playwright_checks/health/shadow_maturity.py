from dataclasses import dataclass


@dataclass(frozen=True)
class ShadowMaturityPolicy:
    """Central, configuration-backed thresholds for shadow migration evidence."""

    overall_coverage_percent: float = 80.0
    critical_coverage_percent: float = 90.0
    executable_coverage_percent: float = 80.0
    result_parity_percent: float = 98.0
    evidence_parity_percent: float = 95.0
    max_policy_regressions: int = 0
    max_executor_errors: int = 0
    max_executor_timeouts: int = 0
    required_consecutive_stable_runs: int = 10
    require_result_parity_sample: bool = True
    require_evidence_parity_sample: bool = True

    @classmethod
    def from_config(cls, health_config=None):
        defaults = cls()
        shadow = (health_config or {}).get("shadow_executor") or {}
        configured = shadow.get("maturity") or {}
        if not isinstance(configured, dict):
            raise TypeError("health_check.shadow_executor.maturity must be a mapping")
        policy = cls(
            overall_coverage_percent=_percentage(
                configured.get(
                    "overall_coverage_percent",
                    defaults.overall_coverage_percent,
                ),
                "overall_coverage_percent",
            ),
            critical_coverage_percent=_percentage(
                configured.get(
                    "critical_coverage_percent",
                    defaults.critical_coverage_percent,
                ),
                "critical_coverage_percent",
            ),
            executable_coverage_percent=_percentage(
                configured.get(
                    "executable_coverage_percent",
                    defaults.executable_coverage_percent,
                ),
                "executable_coverage_percent",
            ),
            result_parity_percent=_percentage(
                configured.get(
                    "result_parity_percent",
                    defaults.result_parity_percent,
                ),
                "result_parity_percent",
            ),
            evidence_parity_percent=_percentage(
                configured.get(
                    "evidence_parity_percent",
                    defaults.evidence_parity_percent,
                ),
                "evidence_parity_percent",
            ),
            max_policy_regressions=_non_negative_int(
                configured.get(
                    "max_policy_regressions",
                    defaults.max_policy_regressions,
                ),
                "max_policy_regressions",
            ),
            max_executor_errors=_non_negative_int(
                configured.get(
                    "max_executor_errors",
                    defaults.max_executor_errors,
                ),
                "max_executor_errors",
            ),
            max_executor_timeouts=_non_negative_int(
                configured.get(
                    "max_executor_timeouts",
                    defaults.max_executor_timeouts,
                ),
                "max_executor_timeouts",
            ),
            required_consecutive_stable_runs=_positive_int(
                configured.get(
                    "required_consecutive_stable_runs",
                    defaults.required_consecutive_stable_runs,
                ),
                "required_consecutive_stable_runs",
            ),
            require_result_parity_sample=_boolean(
                configured.get(
                    "require_result_parity_sample",
                    defaults.require_result_parity_sample,
                ),
                "require_result_parity_sample",
            ),
            require_evidence_parity_sample=_boolean(
                configured.get(
                    "require_evidence_parity_sample",
                    defaults.require_evidence_parity_sample,
                ),
                "require_evidence_parity_sample",
            ),
        )
        return policy

    def to_dict(self):
        return dict(self.__dict__)

    def stable_record(self, record, mapping_consistent=True):
        result_parity = record.get("result_parity")
        evidence_parity = record.get("evidence_parity")
        if self.require_result_parity_sample and result_parity is None:
            return False
        if self.require_evidence_parity_sample and evidence_parity is None:
            return False
        return bool(
            mapping_consistent
            and float(record.get("overall_coverage", 0) or 0)
            >= self.overall_coverage_percent
            and float(record.get("critical_coverage", 0) or 0)
            >= self.critical_coverage_percent
            and float(record.get("executable_coverage", 0) or 0)
            >= self.executable_coverage_percent
            and (result_parity is None or float(result_parity) >= self.result_parity_percent)
            and (
                evidence_parity is None
                or float(evidence_parity) >= self.evidence_parity_percent
            )
            and int(record.get("policy_regressions", 0) or 0)
            <= self.max_policy_regressions
            and int(record.get("executor_errors", 0) or 0)
            <= self.max_executor_errors
            and int(record.get("executor_timeouts", 0) or 0)
            <= self.max_executor_timeouts
        )


def _percentage(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{name} must be numeric") from error
    if not 0 <= normalized <= 100:
        raise ValueError(f"{name} must be between 0 and 100")
    return normalized


def _non_negative_int(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{name} must be a non-negative integer") from error
    if normalized < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return normalized


def _positive_int(value, name):
    normalized = _non_negative_int(value, name)
    if normalized < 1:
        raise ValueError(f"{name} must be a positive integer")
    return normalized


def _boolean(value, name):
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be boolean")
    return value
