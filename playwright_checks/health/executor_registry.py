from dataclasses import dataclass
from typing import Callable

from playwright_checks.health.execution_models import CheckResult, ExecutorContext
from playwright_checks.health.executors import (
    content_text_present,
    dom_control_state,
    dom_descendant_presence,
    dom_element_count,
    dom_element_enabled,
    dom_element_presence,
    dom_element_visible,
    dom_multiple_signal_presence,
    interaction_safe_click,
    navigation_url_reachable,
)


EXECUTOR_REGISTRY_SCHEMA_VERSION = "1.0"


class ExecutorRegistryValidationError(ValueError):
    pass


ExecutorCallable = Callable[[ExecutorContext], CheckResult]


@dataclass(frozen=True)
class ExecutorDefinition:
    executor_key: str
    version: str
    executor: ExecutorCallable
    description: str


class ExecutorRegistry:
    schema_version = EXECUTOR_REGISTRY_SCHEMA_VERSION

    def __init__(self, entries=None):
        self._entries = tuple(DEFAULT_EXECUTORS if entries is None else entries)
        self._by_key = {}
        self.validate()

    @property
    def entries(self):
        return self._entries

    def validate(self):
        selected = {}
        for index, entry in enumerate(self._entries):
            if not isinstance(entry, ExecutorDefinition):
                raise ExecutorRegistryValidationError(
                    f"Executor entry {index} must be ExecutorDefinition"
                )
            key = str(entry.executor_key or "").strip()
            if not key:
                raise ExecutorRegistryValidationError(
                    f"Executor entry {index} has missing executor_key"
                )
            if key in selected:
                raise ExecutorRegistryValidationError(
                    f"Duplicate executor_key: {key}"
                )
            if not str(entry.version or "").strip():
                raise ExecutorRegistryValidationError(
                    f"Executor {key} has missing version"
                )
            if not callable(entry.executor):
                raise ExecutorRegistryValidationError(
                    f"Executor {key} has missing callable"
                )
            if not str(entry.description or "").strip():
                raise ExecutorRegistryValidationError(
                    f"Executor {key} has missing description"
                )
            selected[key] = entry
        self._by_key = selected
        return self

    def resolve(self, executor_key):
        return self._by_key.get(str(executor_key or "").strip())

    def supports(self, executor_key):
        return self.resolve(executor_key) is not None

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "entries": [
                {
                    "executor_key": entry.executor_key,
                    "version": entry.version,
                    "description": entry.description,
                }
                for entry in self._entries
            ],
        }


def _definition(key, executor, description):
    return ExecutorDefinition(
        executor_key=key,
        version="1.0",
        executor=executor,
        description=description,
    )


DEFAULT_EXECUTORS = (
    _definition(
        "dom.element_presence",
        dom_element_presence,
        "Confirm that at least one configured DOM target is attached.",
    ),
    _definition(
        "dom.element_visible",
        dom_element_visible,
        "Confirm that a configured DOM target is visible.",
    ),
    _definition(
        "dom.element_enabled",
        dom_element_enabled,
        "Confirm that a configured DOM control is enabled.",
    ),
    _definition(
        "dom.element_count",
        dom_element_count,
        "Compare matching DOM element count with a configured minimum.",
    ),
    _definition(
        "dom.multiple_signal_presence",
        dom_multiple_signal_presence,
        "Measure a configured set of attached or visible DOM signals.",
    ),
    _definition(
        "dom.descendant_presence",
        dom_descendant_presence,
        "Sample repeated roots for a required visible descendant ratio.",
    ),
    _definition(
        "dom.control_state",
        dom_control_state,
        "Observe control visibility, text and readiness without clicking.",
    ),
    _definition(
        "content.text_present",
        content_text_present,
        "Confirm that a visible target contains non-empty text.",
    ),
    _definition(
        "navigation.url_reachable",
        navigation_url_reachable,
        "Confirm final HTTP URL and available main-document status.",
    ),
    _definition(
        "interaction.safe_click",
        interaction_safe_click,
        "Use a Playwright trial click to verify SAFE actionability.",
    ),
)
