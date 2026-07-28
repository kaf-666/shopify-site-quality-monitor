import re
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

    def test_only_the_gray_scope_is_invoked(self):
        command = re.search(
            r"run_all\.py(?P<args>.+?)(?:'''|\n\s*\))",
            self.content,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(command)
        args = command.group("args")
        self.assertIn("--site mondressy_US", args)
        self.assertIn("--viewport desktop", args)
        self.assertIn("--page home", args)
        self.assertNotIn("--viewport mobile", args)
        self.assertNotIn("--page collection", args)
        self.assertNotIn("--page product", args)

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
