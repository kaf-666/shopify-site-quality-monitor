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

    def test_only_mondressy_bare_host_home_gray_scope_is_invoked(self):
        self.assertIn("target_host=mondressy.com", self.content)
        self.assertIn("signed_request_hosts=mondressy.com", self.content)
        self.assertIn("run_all.py", self.content)
        self.assertIn("--site mondressy_US", self.content)
        self.assertIn("--viewport desktop", self.content)
        self.assertIn("--page home", self.content)
        self.assertNotIn("--viewport mobile", self.content)
        self.assertNotIn("--page collection", self.content)
        self.assertNotIn("--page product", self.content)
        self.assertNotIn(
            "-m playwright_checks.diagnostics.mondressy_429",
            self.content,
        )

    def test_runtime_and_side_effect_controls_are_explicit(self):
        expected = (
            "RUNTIME_HEALTH_REPORT_ONLY = 'true'",
            "RUNTIME_HEALTH_AFFECT_EXIT_CODE = 'false'",
            "ALLOW_BASELINE_INIT = 'false'",
            "FORCE_BASELINE_INIT = 'false'",
            "ALLOW_SIDE_EFFECT_FLOW = 'false'",
            "baseline_init=false",
            "side_effect_flows=false",
            "SCREENSHOT_RETENTION_MODE = 'evidence_only'",
            "VISUAL_STRICT_WARNINGS = 'false'",
        )
        for item in expected:
            self.assertIn(item, self.content)

        forbidden = (
            "add_to_cart_flow",
            "checkout_flow",
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

    def test_nonzero_run_continues_through_summary_archive_and_evaluation(self):
        run_index = self.content.index(
            "stage('Run Mondressy US Runtime Gray Validation')"
        )
        summary_index = self.content.index(
            "stage('Print Runtime Summary')"
        )
        archive_index = self.content.index("stage('Archive Artifacts')")
        cleanup_index = self.content.index(
            "stage('Clean Current Run Temporary Artifacts')"
        )
        evaluate_index = self.content.index("stage('Evaluate Result')")
        self.assertLess(run_index, summary_index)
        self.assertLess(summary_index, archive_index)
        self.assertLess(archive_index, cleanup_index)
        self.assertLess(cleanup_index, evaluate_index)

        run_stage = self.content[run_index:summary_index]
        summary_stage = self.content[summary_index:archive_index]
        archive_stage = self.content[archive_index:evaluate_index]
        self.assertEqual(2, run_stage.count("returnStatus: true"))
        self.assertNotIn("error(", summary_stage)
        self.assertIn("exit 0", summary_stage)
        self.assertIn("exit /b 0", summary_stage)
        self.assertIn("archiveArtifacts(", archive_stage)
        self.assertIn("allowEmptyArchive: true", archive_stage)
        self.assertIn("catch (archiveError)", archive_stage)

    def test_artifact_retention_and_archive_scope_are_bounded(self):
        self.assertIn("buildDiscarder(", self.content)
        for value in (
            "daysToKeepStr: '14'",
            "numToKeepStr: '20'",
            "artifactDaysToKeepStr: '7'",
            "artifactNumToKeepStr: '10'",
        ):
            self.assertIn(value, self.content)
        self.assertIn("artifact-summary.json", self.content)
        self.assertIn("artifact-manifest.json", self.content)
        self.assertIn("runtime/*.json", self.content)
        self.assertIn("current/*.png", self.content)
        self.assertIn("diff/*.png", self.content)
        self.assertIn("excludes:", self.content)
        for forbidden in (
            "**/.tmp/**",
            "**/baselines/**",
            "**/.venv/**",
            "**/__pycache__/**",
        ):
            self.assertIn(forbidden, self.content)
        self.assertNotIn(
            'artifacts: "artifacts/${env.VISUAL_RUN_ID}/**',
            self.content,
        )

    def test_python_exit_code_is_assigned_directly_to_env(self):
        run_index = self.content.index(
            "stage('Run Mondressy US Runtime Gray Validation')"
        )
        summary_index = self.content.index(
            "stage('Print Runtime Summary')"
        )
        run_stage = self.content[run_index:summary_index]
        summary_stage = self.content[
            summary_index:self.content.index("stage('Archive Artifacts')")
        ]

        unix_direct_assignment = re.search(
            r"env\.GRAY_PYTHON_EXIT_CODE\s*=\s*sh\("
            r".*?returnStatus:\s*true\s*"
            r"\)\.toString\(\)",
            run_stage,
            re.DOTALL,
        )
        windows_direct_assignment = re.search(
            r"env\.GRAY_PYTHON_EXIT_CODE\s*=\s*bat\("
            r".*?returnStatus:\s*true\s*"
            r"\)\.toString\(\)",
            run_stage,
            re.DOTALL,
        )
        self.assertIsNotNone(unix_direct_assignment)
        self.assertIsNotNone(windows_direct_assignment)
        self.assertNotIn("def pythonExitCode", self.content)
        self.assertNotIn("int pythonExitCode", self.content)
        self.assertNotIn("String.valueOf(pythonExitCode)", self.content)
        self.assertNotIn(
            "GRAY_PYTHON_EXIT_CODE = 'not_run'",
            self.content,
        )
        self.assertEqual(
            2,
            len(
                re.findall(
                    r"env\.GRAY_PYTHON_EXIT_CODE\s*=\s*(?:sh|bat)\(",
                    self.content,
                )
            ),
        )
        self.assertIn(
            "Captured GRAY_PYTHON_EXIT_CODE=",
            run_stage,
        )
        self.assertIn(
            "def capturedCode =",
            run_stage,
        )
        self.assertIn(
            "capturedCode ==~ /^[0-9]+$/",
            run_stage,
        )
        self.assertIn(
            "Pipeline state error: invalid ",
            run_stage,
        )
        self.assertNotIn(
            "Gray validation Python process did not run.",
            self.content,
        )
        self.assertNotIn("GRAY_PIPELINE_STATE_ERROR", self.content)
        self.assertNotIn(
            "env.GRAY_PYTHON_EXIT_CODE =",
            summary_stage,
        )
        self.assertIn(
            "--python-exit-code '${env.GRAY_PYTHON_EXIT_CODE}'",
            summary_stage,
        )
        self.assertLess(
            self.content.index("stage('Print Runtime Summary')"),
            self.content.index("stage('Archive Artifacts')"),
        )
        self.assertLess(
            self.content.index("stage('Archive Artifacts')"),
            self.content.index("stage('Evaluate Result')"),
        )

    def test_summary_exit_code_uses_workspace_file(self):
        summary_index = self.content.index(
            "stage('Print Runtime Summary')"
        )
        archive_index = self.content.index("stage('Archive Artifacts')")
        evaluate_index = self.content.index("stage('Evaluate Result')")
        post_index = self.content.index("    post {")
        summary_stage = self.content[summary_index:archive_index]
        after_summary = self.content[archive_index:]
        post_block = self.content[post_index:]

        self.assertIn(
            "def exitCodeFile = '.gray_summary_exit_code'",
            summary_stage,
        )
        self.assertIn("summary_code=\\$?", summary_stage)
        self.assertIn("%ERRORLEVEL%", summary_stage)
        self.assertIn(
            "printf '%s' \"\\$summary_code\"",
            summary_stage,
        )
        self.assertIn(
            '> "${exitCodeFile}" echo %summary_code%',
            summary_stage,
        )
        self.assertIn("exit 0", summary_stage)
        self.assertIn("exit /b 0", summary_stage)
        self.assertIn("fileExists(exitCodeFile)", summary_stage)
        self.assertIn("readFile(", summary_stage)
        self.assertNotIn("returnStatus: true", summary_stage)
        self.assertNotIn("error(", summary_stage)
        self.assertNotIn("int summaryExitCode", self.content)
        self.assertNotIn("def summaryExitCode", self.content)
        self.assertNotIn("String.valueOf(summaryExitCode)", self.content)
        self.assertNotRegex(
            summary_stage,
            r"env\.GRAY_SUMMARY_EXIT_CODE\s*=\s*(?:sh|bat)\(",
        )
        self.assertEqual(
            1,
            self.content.count(
                "GRAY_SUMMARY_EXIT_CODE = 'not_run'"
            ),
        )
        self.assertEqual(
            2,
            summary_stage.count(
                "env.GRAY_SUMMARY_EXIT_CODE = '98'"
            ),
        )
        self.assertIn(
            "Captured GRAY_SUMMARY_EXIT_CODE=",
            summary_stage,
        )
        self.assertIn(
            "env.GRAY_SUMMARY_EXIT_CODE = capturedCode",
            summary_stage,
        )
        self.assertIn(
            "def capturedCode = ''",
            summary_stage,
        )
        self.assertIn(
            "capturedCode ==~ /^[0-9]+$/",
            summary_stage,
        )
        self.assertIn(
            "Raw summary exit-code file content=",
            summary_stage,
        )
        self.assertIn("capturedCode.length()", summary_stage)
        self.assertNotIn("/^\\d+$/", self.content)
        self.assertIn(
            "Invalid summary exit-code file content: ",
            summary_stage,
        )
        self.assertIn(
            "Summary exit-code file was not created.",
            summary_stage,
        )
        self.assertNotRegex(
            after_summary,
            r"env\.GRAY_SUMMARY_EXIT_CODE\s*=(?!=|~)",
        )
        self.assertNotRegex(
            post_block,
            r"GRAY_SUMMARY_EXIT_CODE\s*=(?!=|~)",
        )
        self.assertLess(summary_index, archive_index)
        self.assertLess(archive_index, evaluate_index)

    def test_summary_exit_code_file_is_cleaned_before_evaluate(self):
        cleanup_index = self.content.index(
            "stage('Clean Current Run Temporary Artifacts')"
        )
        evaluate_index = self.content.index("stage('Evaluate Result')")
        cleanup_stage = self.content[cleanup_index:evaluate_index]

        self.assertIn(".gray_summary_exit_code", cleanup_stage)
        self.assertIn("rm -f --", cleanup_stage)
        self.assertIn("@del /q", cleanup_stage)
        self.assertIn("returnStatus: true", cleanup_stage)
        self.assertIn(
            "Warning: failed to remove summary exit-code ",
            cleanup_stage,
        )
        self.assertNotIn("error(", cleanup_stage)
        self.assertLess(cleanup_index, evaluate_index)

    def test_evaluate_summary_exit_code_matrix(self):
        evaluate_index = self.content.index("stage('Evaluate Result')")
        post_index = self.content.index("    post {")
        evaluate_stage = self.content[evaluate_index:post_index]

        self.assertIn(
            "def summaryCode =",
            evaluate_stage,
        )
        self.assertIn(
            "summaryCode ==~ /^[0-9]+$/",
            evaluate_stage,
        )
        self.assertIn(
            "summaryCode != '0'",
            evaluate_stage,
        )
        self.assertIn(
            "Pipeline state error: invalid ",
            evaluate_stage,
        )
        self.assertIn(
            "Runtime gray summary validation failed ",
            evaluate_stage,
        )
        self.assertNotIn(
            "env.GRAY_SUMMARY_EXIT_CODE =",
            evaluate_stage,
        )
        self.assertEqual(
            evaluate_stage.count("if ("),
            evaluate_stage.count("error("),
        )

        def expected_evaluate_action(value):
            captured = (value or "").strip()
            if not re.fullmatch(r"[0-9]+", captured):
                return "pipeline_state_error"
            if captured != "0":
                return f"summary_error:{captured}"
            return "success"

        cases = (
            ("0", "success"),
            ("1", "summary_error:1"),
            ("2", "summary_error:2"),
            ("98", "summary_error:98"),
            ("not_run", "pipeline_state_error"),
            ("", "pipeline_state_error"),
        )
        for value, expected in cases:
            with self.subTest(summary_exit_code=value):
                self.assertEqual(
                    expected,
                    expected_evaluate_action(value),
                )

    def test_summary_exit_code_file_content_matrix(self):
        def captured_env_value(file_content):
            captured = (file_content or "").strip()
            if re.fullmatch(r"[0-9]+", captured):
                return captured
            return "98"

        cases = (
            ("0", "0"),
            ("1", "1"),
            ("2", "2"),
            ("0\n", "0"),
            ("", "98"),
            ("not_run", "98"),
        )
        for file_content, expected in cases:
            with self.subTest(file_content=repr(file_content)):
                self.assertEqual(
                    expected,
                    captured_env_value(file_content),
                )

    def test_monitor_command_is_inside_with_credentials(self):
        run_index = self.content.index(
            "stage('Run Mondressy US Runtime Gray Validation')"
        )
        summary_index = self.content.index(
            "stage('Print Runtime Summary')"
        )
        run_stage = self.content[run_index:summary_index]
        credentials_index = run_stage.index("withCredentials([")
        command_index = run_stage.index("run_all.py")
        credentials_close_index = run_stage.rindex("}\n")
        self.assertLess(credentials_index, command_index)
        self.assertLess(command_index, credentials_close_index)

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

    def test_production_header_injection_remains_route_based(self):
        self.assertIn("request_header_injection=route", self.content)
        self.assertNotIn(
            "request_header_injection=extra_http_headers",
            self.content,
        )

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
