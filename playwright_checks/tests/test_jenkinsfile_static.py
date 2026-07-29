import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
JENKINSFILE = PROJECT_ROOT / "Jenkinsfile"


class JenkinsfileStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = JENKINSFILE.read_text(encoding="utf-8")

    def test_declarative_structure_is_balanced(self):
        self.assertIn("pipeline {", self.content)
        for opening, closing in (("{", "}"), ("(", ")"), ("[", "]")):
            self.assertEqual(
                self.content.count(opening),
                self.content.count(closing),
                f"unbalanced {opening}{closing}",
            )

    def test_only_the_six_probe_diagnostic_is_invoked(self):
        self.assertIn(
            "-m playwright_checks.diagnostics.mondressy_429",
            self.content,
        )
        self.assertIn("probe_order=curl,APIRequest,Chromium", self.content)
        self.assertIn(
            "hosts=mondressy.com,www.mondressy.com",
            self.content,
        )
        self.assertNotIn("run_all.py", self.content)
        self.assertNotIn("gray_summary", self.content)

    def test_runtime_gate_and_side_effect_controls_are_explicit(self):
        expected = (
            "RUNTIME_HEALTH_REPORT_ONLY = 'true'",
            "RUNTIME_HEALTH_AFFECT_EXIT_CODE = 'false'",
            "ALLOW_BASELINE_INIT = 'false'",
            "FORCE_BASELINE_INIT = 'false'",
            "ALLOW_SIDE_EFFECT_FLOW = 'false'",
        )
        for item in expected:
            self.assertIn(item, self.content)

    def test_visual_and_side_effect_work_are_explicitly_disabled(self):
        self.assertIn("visual_checks=false", self.content)
        self.assertIn("side_effect_flows=false", self.content)
        forbidden = (
            "--page home",
            "--page collection",
            "--page product",
            "add_to_cart_flow",
        )
        for item in forbidden:
            self.assertNotIn(item, self.content.lower())

    def test_no_automatic_trigger_is_configured(self):
        forbidden = (
            "triggers {",
            "cron(",
            "pollSCM(",
            "upstream(",
            "GenericTrigger(",
        )
        for item in forbidden:
            self.assertNotIn(item, self.content)
        self.assertIn("Cause$UserIdCause", self.content)

    def test_chromium_is_installed_and_smoke_launched(self):
        self.assertIn(
            "PLAYWRIGHT_BROWSER_CHANNEL = 'chromium'",
            self.content,
        )
        self.assertIn("-m playwright install chromium", self.content)
        self.assertIn("p.chromium.launch(headless=True)", self.content)

    def test_archive_is_non_blocking_in_post_always(self):
        post_index = self.content.index("post {")
        always_index = self.content.index("always {", post_index)
        archive_index = self.content.index("archiveArtifacts(", always_index)
        catch_index = self.content.index("catch (archiveError)", archive_index)
        self.assertLess(post_index, always_index)
        self.assertLess(always_index, archive_index)
        self.assertLess(archive_index, catch_index)
        self.assertIn("allowEmptyArchive: true", self.content)

    def test_credentials_are_bound_without_environment_dump_commands(self):
        for name in (
            "MONDRESSY_US_SHOPIFY_SIGNATURE",
            "MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT",
            "MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT",
        ):
            self.assertIn(f"credentialsId: '{name}'", self.content)
            self.assertIn(f"variable: '{name}'", self.content)

        forbidden_dump_commands = (
            "printenv",
            "Get-ChildItem Env:",
            "set >",
            "env >",
            "export -p",
        )
        for command in forbidden_dump_commands:
            self.assertNotIn(command, self.content)

    def test_diagnostic_uses_extra_headers_and_no_route_injection(self):
        self.assertIn(
            "request_header_injection=extra_http_headers",
            self.content,
        )
        self.assertIn("chromium_route_injection=false", self.content)

    def test_dynamic_environment_access_is_forbidden(self):
        self.assertNotIn("env[", self.content)
        self.assertNotIn("env.getAt(", self.content)
        for name in (
            "MONDRESSY_US_SHOPIFY_SIGNATURE",
            "MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT",
            "MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT",
        ):
            self.assertIn(f"env.{name}?.trim()", self.content)


if __name__ == "__main__":
    unittest.main()
