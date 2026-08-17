from dataclasses import dataclass

from playwright_checks.health.capabilities import canonical_capability_name
from playwright_checks.health.models import (
    EvidenceType,
    PageType,
    Severity,
    SideEffectLevel,
    serialize,
)


CAPABILITY_REGISTRY_SCHEMA_VERSION = "1.0"


class RegistryValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CapabilityCheck:
    check_id: str
    capability: str
    applicable_page_types: tuple[PageType, ...]
    interaction_policy: SideEffectLevel
    severity: Severity
    executor: str
    evidence_requirements: tuple[EvidenceType, ...]
    enabled_by_default: bool = True

    def to_dict(self):
        return serialize(self)


class CapabilityCheckRegistry:
    """Static Python registry from profile capabilities to check contracts."""

    schema_version = CAPABILITY_REGISTRY_SCHEMA_VERSION

    def __init__(self, entries=None):
        self._entries = tuple(
            DEFAULT_CAPABILITY_CHECKS if entries is None else entries
        )
        self.validate()

    @property
    def entries(self):
        return self._entries

    def validate(self):
        check_ids = set()
        for index, entry in enumerate(self._entries):
            if not isinstance(entry, CapabilityCheck):
                raise RegistryValidationError(
                    f"Registry entry {index} must be CapabilityCheck"
                )
            if not entry.check_id.strip():
                raise RegistryValidationError(
                    f"Registry entry {index} has missing check_id"
                )
            if entry.check_id in check_ids:
                raise RegistryValidationError(
                    f"Duplicate check_id: {entry.check_id}"
                )
            check_ids.add(entry.check_id)
            if not canonical_capability_name(entry.capability):
                raise RegistryValidationError(
                    f"Registry entry {entry.check_id} has missing capability"
                )
            if not entry.applicable_page_types:
                raise RegistryValidationError(
                    f"Registry entry {entry.check_id} has no applicable page types"
                )
            if any(
                not isinstance(page_type, PageType)
                for page_type in entry.applicable_page_types
            ):
                raise RegistryValidationError(
                    f"Registry entry {entry.check_id} has invalid page type"
                )
            if not isinstance(entry.executor, str) or not entry.executor.strip():
                raise RegistryValidationError(
                    f"Registry entry {entry.check_id} has missing executor"
                )
            if not isinstance(entry.interaction_policy, SideEffectLevel):
                raise RegistryValidationError(
                    f"Registry entry {entry.check_id} has invalid interaction policy"
                )
            if not isinstance(entry.severity, Severity):
                raise RegistryValidationError(
                    f"Registry entry {entry.check_id} has invalid severity"
                )
            if not entry.evidence_requirements or any(
                not isinstance(value, EvidenceType)
                for value in entry.evidence_requirements
            ):
                raise RegistryValidationError(
                    f"Registry entry {entry.check_id} has invalid evidence requirements"
                )
            if not isinstance(entry.enabled_by_default, bool):
                raise RegistryValidationError(
                    f"Registry entry {entry.check_id} enabled_by_default must be boolean"
                )
        return self

    def checks_for(self, capability, page_type):
        normalized = canonical_capability_name(capability)
        if not isinstance(page_type, PageType):
            try:
                page_type = PageType(str(page_type).upper())
            except ValueError:
                return []
        return [
            entry
            for entry in self._entries
            if canonical_capability_name(entry.capability) == normalized
            and page_type in entry.applicable_page_types
        ]

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "entries": [entry.to_dict() for entry in self._entries],
        }


def _check(
    check_id,
    capability,
    page_types,
    executor,
    evidence,
    severity=Severity.MEDIUM,
    interaction=SideEffectLevel.SAFE,
    enabled=True,
):
    return CapabilityCheck(
        check_id=check_id,
        capability=canonical_capability_name(capability),
        applicable_page_types=tuple(page_types),
        interaction_policy=interaction,
        severity=severity,
        executor=executor,
        evidence_requirements=tuple(evidence),
        enabled_by_default=enabled,
    )


CONTENT_LIKE_PAGES = (
    PageType.HOME,
    PageType.PLP,
    PageType.PDP,
    PageType.SEARCH,
    PageType.CART,
    PageType.LOGIN,
    PageType.ACCOUNT,
    PageType.CHECKOUT_ENTRY,
    PageType.CONTENT,
    PageType.OTHER,
)


DEFAULT_CAPABILITY_CHECKS = (
    _check(
        "global.navigation.health",
        "navigation",
        CONTENT_LIKE_PAGES,
        "navigation.url_reachable",
        (EvidenceType.URL, EvidenceType.HTTP),
        severity=Severity.HIGH,
    ),
    _check(
        "home.navigation.presence",
        "navigation",
        (PageType.HOME,),
        "dom.element_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "home.navigation.visible",
        "navigation",
        (PageType.HOME,),
        "dom.element_visible",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "home.core_modules.health",
        "core_modules",
        (PageType.HOME,),
        "dom.multiple_signal_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
        severity=Severity.HIGH,
    ),
    _check(
        "home.presence_signals.health",
        "presence_signals",
        (PageType.HOME,),
        "dom.multiple_signal_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
    ),
    _check(
        "home.plugin_signals.health",
        "plugin_signals",
        (PageType.HOME,),
        "dom.multiple_signal_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
        severity=Severity.LOW,
    ),
    _check(
        "home.main_content.health",
        "main_content",
        (PageType.HOME,),
        "content.text_present",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "home.hero.health",
        "hero",
        (PageType.HOME,),
        "dom.element_visible",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
    ),
    _check(
        "home.mobile_navigation.control_presence",
        "mobile_menu",
        (PageType.HOME,),
        "dom.element_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.LOW,
    ),
    _check(
        "home.mega_menu.health",
        "mega_menu",
        (PageType.HOME,),
        "legacy.home.dom_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
    ),
    _check(
        "search.basic_health",
        "search",
        (PageType.HOME, PageType.SEARCH),
        "legacy.home.dom_modules",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "plp.product_grid.health",
        "product_grid",
        (PageType.PLP, PageType.SEARCH),
        "dom.element_count",
        (EvidenceType.DOM, EvidenceType.METRIC),
        severity=Severity.HIGH,
    ),
    _check(
        "plp.product_grid.visible",
        "product_grid",
        (PageType.PLP,),
        "dom.element_visible",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "plp.product_card.presence",
        "product_card",
        (PageType.PLP,),
        "dom.element_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "plp.product_card.title_presence",
        "product_card_title",
        (PageType.PLP,),
        "dom.descendant_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
        severity=Severity.HIGH,
    ),
    _check(
        "plp.product_card.price_presence",
        "product_card_price",
        (PageType.PLP,),
        "dom.descendant_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
        severity=Severity.HIGH,
    ),
    _check(
        "plp.product_card.image_presence",
        "product_card_image",
        (PageType.PLP,),
        "dom.descendant_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
        severity=Severity.HIGH,
    ),
    _check(
        "plp.core_modules.health",
        "core_modules",
        (PageType.PLP,),
        "dom.multiple_signal_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
        severity=Severity.HIGH,
    ),
    _check(
        "plp.presence_signals.health",
        "presence_signals",
        (PageType.PLP,),
        "dom.multiple_signal_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
    ),
    _check(
        "plp.filter.health",
        "filter",
        (PageType.PLP, PageType.SEARCH),
        "dom.element_visible",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
    ),
    _check(
        "plp.sort.health",
        "sort",
        (PageType.PLP, PageType.SEARCH),
        "dom.element_visible",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
    ),
    _check(
        "plp.pagination.health",
        "pagination",
        (PageType.PLP, PageType.SEARCH),
        "dom.element_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
    ),
    _check(
        "plp.load_more.health",
        "load_more",
        (PageType.PLP, PageType.SEARCH),
        "legacy.collection.pagination",
        (EvidenceType.SELECTOR, EvidenceType.METRIC),
    ),
    _check(
        "pdp.product_title.presence",
        "product_title",
        (PageType.PDP,),
        "content.text_present",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "pdp.product_price.presence",
        "product_price",
        (PageType.PDP,),
        "content.text_present",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "pdp.gallery.health",
        "product_gallery",
        (PageType.PDP,),
        "dom.element_visible",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "pdp.product_main_image.health",
        "product_main_image",
        (PageType.PDP,),
        "dom.descendant_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
        severity=Severity.HIGH,
    ),
    _check(
        "pdp.product_info.health",
        "product_info",
        (PageType.PDP,),
        "dom.element_visible",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "pdp.product_form.health",
        "product_form",
        (PageType.PDP,),
        "dom.element_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "pdp.core_modules.health",
        "core_modules",
        (PageType.PDP,),
        "dom.multiple_signal_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
        severity=Severity.HIGH,
    ),
    _check(
        "pdp.presence_signals.health",
        "presence_signals",
        (PageType.PDP,),
        "dom.multiple_signal_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
    ),
    _check(
        "pdp.size_selector.health",
        "size_selector",
        (PageType.PDP,),
        "dom.element_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "pdp.color_selector.health",
        "color_selector",
        (PageType.PDP,),
        "dom.element_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "pdp.variant_selector.health",
        "variant_selector",
        (PageType.PDP,),
        "dom.element_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "pdp.quantity_selector.health",
        "quantity_selector",
        (PageType.PDP,),
        "legacy.product.dom_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
    ),
    _check(
        "commerce.add_to_cart.control_health",
        "add_to_cart_control",
        (PageType.PDP,),
        "dom.control_state",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.METRIC),
        severity=Severity.HIGH,
        interaction=SideEffectLevel.SAFE,
    ),
    _check(
        "commerce.add_to_cart.action_health",
        "add_to_cart_action",
        (PageType.PDP,),
        "planned.commerce.add_to_cart.action",
        (EvidenceType.SELECTOR, EvidenceType.DOM, EvidenceType.LOG),
        severity=Severity.HIGH,
        interaction=SideEffectLevel.TRANSACTIONAL_SAFE,
    ),
    _check(
        "commerce.buy_now.health",
        "buy_now",
        (PageType.PDP,),
        "planned.commerce.buy_now",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.HIGH,
        interaction=SideEffectLevel.HIGH_RISK,
        enabled=False,
    ),
    _check(
        "commerce.cart_drawer.health",
        "cart_drawer",
        (PageType.HOME, PageType.PLP, PageType.PDP),
        "legacy.home.dom_modules",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        interaction=SideEffectLevel.TRANSACTIONAL_SAFE,
    ),
    _check(
        "commerce.cart_page.health",
        "cart_page",
        (PageType.CART,),
        "planned.cart.page_health",
        (EvidenceType.URL, EvidenceType.DOM),
        severity=Severity.HIGH,
    ),
    _check(
        "commerce.cart_quantity.health",
        "cart_quantity",
        (PageType.CART,),
        "planned.cart.quantity",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        interaction=SideEffectLevel.TRANSACTIONAL_SAFE,
    ),
    _check(
        "commerce.cart_remove.health",
        "cart_remove",
        (PageType.CART,),
        "planned.cart.remove",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        interaction=SideEffectLevel.TRANSACTIONAL_SAFE,
    ),
    _check(
        "pdp.reviews.health",
        "reviews",
        (PageType.PDP,),
        "legacy.product.dom_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.LOW,
    ),
    _check(
        "pdp.shipping_info.health",
        "shipping_info",
        (PageType.PDP,),
        "legacy.product.dom_presence",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.LOW,
    ),
    _check(
        "ui.accordion.health",
        "accordion",
        (PageType.PDP, PageType.CONTENT, PageType.OTHER),
        "legacy.structure.readonly_interaction",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.LOW,
    ),
    _check(
        "ui.modal.health",
        "modal",
        CONTENT_LIKE_PAGES,
        "legacy.structure.readonly_interaction",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.LOW,
    ),
    _check(
        "ui.popup.health",
        "popup",
        CONTENT_LIKE_PAGES,
        "legacy.structure.readonly_interaction",
        (EvidenceType.SELECTOR, EvidenceType.DOM),
        severity=Severity.LOW,
    ),
    _check(
        "account.login.health",
        "login",
        (PageType.LOGIN,),
        "planned.account.login",
        (EvidenceType.URL, EvidenceType.DOM),
        severity=Severity.HIGH,
        interaction=SideEffectLevel.HIGH_RISK,
        enabled=False,
    ),
    _check(
        "checkout.entry.health",
        "checkout_entry",
        (PageType.CHECKOUT_ENTRY,),
        "planned.checkout.entry",
        (EvidenceType.URL, EvidenceType.DOM),
        severity=Severity.HIGH,
        interaction=SideEffectLevel.HIGH_RISK,
        enabled=False,
    ),
    _check(
        "checkout.submit.health",
        "checkout_submit",
        (PageType.CHECKOUT_ENTRY,),
        "planned.checkout.submit",
        (EvidenceType.URL, EvidenceType.DOM, EvidenceType.LOG),
        severity=Severity.CRITICAL,
        interaction=SideEffectLevel.HIGH_RISK,
        enabled=False,
    ),
)
