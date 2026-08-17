import copy
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from playwright_checks.core.config_loader import load_site_config
from playwright_checks.health.capability_registry import (
    CapabilityCheckRegistry,
    RegistryValidationError,
)
from playwright_checks.health.planner import PlanDisposition, TestPlanner
from playwright_checks.health.profile_adapter import LegacySiteConfigAdapter
from playwright_checks.health.site_profile import (
    CapabilityStatus,
    SitePlatform,
    SiteProfile,
    SiteType,
)
from playwright_checks.health.models import HealthStatus, PageType
from playwright_checks.runner import main as runner


SITE_NAMES = (
    "gracins_US",
    "lavetir_US",
    "mondressy_UK",
    "mondressy_US",
    "nafori_US",
    "shirees_US",
)


FIXTURE_SITE = {
    "site": "fixture_US",
    "base_url": "https://fixture.example",
    "pages": {
        "home": {
            "url": "https://fixture.example/",
            "modules": {
                "header_1": ["css", "header"],
                "search_btn": ["css", ".search"],
            },
        },
        "collection": {
            "url": "https://fixture.example/collections/all",
            "modules": {
                "product_grid": ["css", ".grid"],
                "filter": ["css", ".filter"],
                "pagination": ["css", ".pagination"],
            },
            "product_card": ["css", ".card"],
        },
        "product": {
            "url": "https://fixture.example/products/example",
            "modules": {
                "gallery": ["css", ".gallery"],
                "info": ["css", ".info"],
                "add_to_cart": ["css", "button[name='add']"],
            },
            "variant_inputs": ["css", "input[name='Size']"],
            "variant_check": {
                "enabled": True,
                "option_name": "Size",
                "option_value": "M",
            },
        },
    },
}


def build_profile(site_config=None):
    return LegacySiteConfigAdapter(
        copy.deepcopy(site_config or FIXTURE_SITE)
    ).build(generated_at="2026-08-14T00:00:00.000+00:00")


def with_capability_status(capability, status, page="product", **extra):
    config = copy.deepcopy(FIXTURE_SITE)
    config["site_profile"] = {
        "capabilities": {
            "pages": {
                page: {
                    capability: {
                        "status": status,
                        **extra,
                    }
                }
            }
        }
    }
    return build_profile(config)


def planned(plan, check_id):
    return next(check for check in plan.checks if check.check_id == check_id)


class ExistingSiteProfileAdapterTests(unittest.TestCase):
    def test_all_six_existing_sites_convert_without_new_configuration(self):
        for site_name in SITE_NAMES:
            with self.subTest(site=site_name):
                profile = LegacySiteConfigAdapter(
                    load_site_config(site_name)
                ).build()
                self.assertEqual(SiteType.ECOMMERCE, profile.site_identity.site_type)
                self.assertEqual(SitePlatform.SHOPIFY, profile.site_identity.platform)
                self.assertEqual(3, len(profile.pages))
                self.assertTrue(profile.capabilities)
                profile.validate()

    def test_legacy_home_plp_pdp_page_types_are_preserved(self):
        profile = build_profile()

        self.assertEqual(
            {
                "home": PageType.HOME,
                "collection": PageType.PLP,
                "product": PageType.PDP,
            },
            {page.page_id: page.page_type for page in profile.pages},
        )
        self.assertTrue(all(page.representative for page in profile.pages))

    def test_adapter_does_not_mutate_legacy_config_or_runner_scope(self):
        config = copy.deepcopy(FIXTURE_SITE)
        before = copy.deepcopy(config)
        LegacySiteConfigAdapter(config).build()

        self.assertEqual(before, config)
        self.assertEqual(
            ("home", "collection", "product"),
            tuple(page[1] for page in runner.ALL_PAGES),
        )
        with patch.dict(os.environ, {"VISUAL_PAGE": "all"}, clear=False):
            self.assertEqual(runner.ALL_PAGES, runner.get_run_pages())

    def test_site_profile_json_round_trip_preserves_contract(self):
        profile = build_profile()
        payload = profile.to_dict()
        serialized = json.dumps(payload, ensure_ascii=False)
        restored = SiteProfile.from_dict(json.loads(serialized))

        self.assertEqual(payload, restored.to_dict())
        self.assertEqual("1.0", restored.schema_version)
        self.assertTrue(restored.site_capabilities())
        self.assertTrue(restored.page_capabilities("product"))


class PlannerContractTests(unittest.TestCase):
    def test_present_capability_generates_registered_check(self):
        plan = TestPlanner().build_plan(build_profile())

        title = planned(plan, "pdp.product_title.presence")
        grid = planned(plan, "plp.product_grid.health")
        self.assertEqual(PlanDisposition.READY, title.disposition)
        self.assertTrue(title.should_execute)
        self.assertEqual(PlanDisposition.READY, grid.disposition)

    def test_absent_capability_does_not_generate_check(self):
        profile = with_capability_status("product_title", "ABSENT")
        plan = TestPlanner().build_plan(profile)

        self.assertNotIn(
            "pdp.product_title.presence",
            {check.check_id for check in plan.checks},
        )

    def test_unknown_capability_is_unverified_not_failed(self):
        profile = with_capability_status("product_price", "UNKNOWN")
        check = planned(
            TestPlanner().build_plan(profile),
            "pdp.product_price.presence",
        )

        self.assertEqual(PlanDisposition.CAPABILITY_UNKNOWN, check.disposition)
        self.assertEqual(HealthStatus.UNVERIFIED, check.status)
        self.assertFalse(check.should_execute)
        self.assertNotEqual(HealthStatus.FAIL, check.status)

    def test_not_applicable_capability_is_retained_but_not_executed(self):
        profile = with_capability_status(
            "size_selector",
            "NOT_APPLICABLE",
        )
        check = planned(
            TestPlanner().build_plan(profile),
            "pdp.size_selector.health",
        )

        self.assertEqual(PlanDisposition.NOT_APPLICABLE, check.disposition)
        self.assertEqual(HealthStatus.NOT_APPLICABLE, check.status)
        self.assertFalse(check.should_execute)

    def test_safe_interaction_is_planned_normally(self):
        check = planned(
            TestPlanner().build_plan(build_profile()),
            "pdp.variant_selector.health",
        )

        self.assertEqual(PlanDisposition.READY, check.disposition)
        self.assertTrue(check.interaction_allowed)
        self.assertTrue(check.should_execute)

    def test_transactional_interaction_requires_explicit_opt_in(self):
        profile = build_profile()
        default_check = planned(
            TestPlanner().build_plan(profile),
            "commerce.add_to_cart.action_health",
        )
        opted_in = planned(
            TestPlanner().build_plan(
                profile,
                explicit_interactions={"add_to_cart_action"},
            ),
            "commerce.add_to_cart.action_health",
        )

        self.assertEqual(PlanDisposition.POLICY_BLOCKED, default_check.disposition)
        self.assertEqual(HealthStatus.UNVERIFIED, default_check.status)
        self.assertFalse(default_check.should_execute)
        self.assertEqual(PlanDisposition.READY, opted_in.disposition)
        self.assertTrue(opted_in.should_execute)

    def test_high_risk_is_denied_even_when_explicit_by_default(self):
        profile = with_capability_status("buy_now", "PRESENT")
        default_registry = CapabilityCheckRegistry()
        buy_now = next(
            entry
            for entry in default_registry.entries
            if entry.check_id == "commerce.buy_now.health"
        )
        registry = CapabilityCheckRegistry(
            [replace(buy_now, enabled_by_default=True)]
        )
        check = planned(
            TestPlanner(registry=registry).build_plan(
                profile,
                explicit_interactions={"buy_now"},
            ),
            "commerce.buy_now.health",
        )

        self.assertEqual(PlanDisposition.POLICY_BLOCKED, check.disposition)
        self.assertFalse(check.interaction_allowed)
        self.assertFalse(check.should_execute)

    def test_unknown_future_capability_does_not_crash_planner(self):
        profile = with_capability_status("future_widget", "PRESENT")
        plan = TestPlanner().build_plan(profile)

        self.assertTrue(
            any(
                value["capability"] == "future_widget"
                for value in plan.unmapped_capabilities
            )
        )

    def test_site_interaction_override_can_enable_transactional_capability(self):
        config = copy.deepcopy(FIXTURE_SITE)
        config["health_check"] = {
            "interaction_policy": {
                "capability_overrides": {"add_to_cart_action": "SAFE"}
            }
        }
        profile = build_profile(config)
        check = planned(
            TestPlanner().build_plan(profile),
            "commerce.add_to_cart.action_health",
        )

        self.assertEqual(PlanDisposition.READY, check.disposition)
        self.assertTrue(check.should_execute)


class RegistryValidationTests(unittest.TestCase):
    def test_registry_rejects_duplicate_check_id(self):
        entry = CapabilityCheckRegistry().entries[0]

        with self.assertRaisesRegex(
            RegistryValidationError,
            "Duplicate check_id",
        ):
            CapabilityCheckRegistry([entry, entry])

    def test_registry_rejects_missing_executor(self):
        entry = CapabilityCheckRegistry().entries[0]

        with self.assertRaisesRegex(
            RegistryValidationError,
            "missing executor",
        ):
            CapabilityCheckRegistry([replace(entry, executor="")])


if __name__ == "__main__":
    unittest.main()
