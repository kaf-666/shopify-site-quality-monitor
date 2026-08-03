pipeline {
    agent any

    options {
        buildDiscarder(
            logRotator(
                daysToKeepStr: '14',
                numToKeepStr: '20',
                artifactDaysToKeepStr: '7',
                artifactNumToKeepStr: '10'
            )
        )
        timestamps()
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
    }

    environment {
        VISUAL_RUN_ID = "jenkins-${BUILD_NUMBER}-mondressy-us-runtime-gray"
        PLAYWRIGHT_BROWSER_CHANNEL = 'chromium'
        RUNTIME_HEALTH_ENABLED = 'true'
        RUNTIME_HEALTH_REPORT_ONLY = 'true'
        RUNTIME_HEALTH_AFFECT_EXIT_CODE = 'false'
        VISUAL_STRICT_WARNINGS = 'false'
        SCREENSHOT_RETENTION_MODE = 'evidence_only'
        ALLOW_BASELINE_INIT = 'false'
        FORCE_BASELINE_INIT = 'false'
        ALLOW_SIDE_EFFECT_FLOW = 'false'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Environment Check') {
            steps {
                script {
                    def manualCauses = currentBuild.getBuildCauses(
                        'hudson.model.Cause$UserIdCause'
                    )
                    if (manualCauses.isEmpty()) {
                        error(
                            'This gray validation only permits a manual ' +
                            'Jenkins user trigger.'
                        )
                    }

                    withCredentials([
                        string(
                            credentialsId: 'MONDRESSY_US_SHOPIFY_SIGNATURE',
                            variable: 'MONDRESSY_US_SHOPIFY_SIGNATURE'
                        ),
                        string(
                            credentialsId: 'MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT',
                            variable: 'MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT'
                        ),
                        string(
                            credentialsId: 'MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT',
                            variable: 'MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT'
                        )
                    ]) {
                        def missing = []

                        if (!env.MONDRESSY_US_SHOPIFY_SIGNATURE?.trim()) {
                            missing.add('MONDRESSY_US_SHOPIFY_SIGNATURE')
                        }

                        if (!env.MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT?.trim()) {
                            missing.add(
                                'MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT'
                            )
                        }

                        if (!env.MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT?.trim()) {
                            missing.add(
                                'MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT'
                            )
                        }

                        if (!missing.isEmpty()) {
                            error(
                                'Missing required environment variables: ' +
                                missing.join(', ')
                            )
                        }
                    }

                    echo 'site_key=mondressy_US'
                    echo 'target_host=mondressy.com'
                    echo 'pages=home,collection,product'
                    echo 'viewports=desktop,mobile'
                    echo 'report_only=true'
                    echo 'runtime_exit_gate=false'
                    echo 'request_header_injection=route'
                    echo 'signed_request_hosts=mondressy.com'
                    echo 'http_cache_mode=disabled_by_routing'
                    echo 'run_profile=intercepted_cold_context'
                    echo 'baseline_init=false'
                    echo 'side_effect_flows=false'
                    echo 'screenshot_retention_mode=evidence_only'
                    echo 'visual_strict_warnings=false'
                }
            }
        }

        stage('Install Dependencies') {
            steps {
                script {
                    if (isUnix()) {
                        sh '''
                            python3 -m venv .venv
                            .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
                            .venv/bin/python -m playwright install chromium
                            .venv/bin/python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(headless=True); b.close(); p.stop(); print('Playwright Chromium launch=OK')"
                        '''
                    } else {
                        bat '''
                            @py -3 -m venv .venv
                            @.venv\\Scripts\\python.exe -m pip install --disable-pip-version-check -r requirements.txt
                            @.venv\\Scripts\\python.exe -m playwright install chromium
                            @.venv\\Scripts\\python.exe -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(headless=True); b.close(); p.stop(); print('Playwright Chromium launch=OK')"
                        '''
                    }
                }
            }
        }

        stage('Clean Old Workspace Artifact Runs') {
            steps {
                script {
                    if (isUnix()) {
                        sh(
                            script: '''
                                .venv/bin/python -u \
                                    -m playwright_checks.artifacts.cleanup \
                                    --keep-run "$VISUAL_RUN_ID" \
                                    --run-pattern "jenkins-*-mondressy-us-runtime-gray"
                            '''
                        )
                    } else {
                        bat(
                            script: '''
                                @.venv\\Scripts\\python.exe -u -m playwright_checks.artifacts.cleanup --keep-run "%VISUAL_RUN_ID%" --run-pattern "jenkins-*-mondressy-us-runtime-gray"
                            '''
                        )
                    }
                }
            }
        }

        stage('Run Mondressy US Runtime Gray Validation') {
            steps {
                withCredentials([
                    string(
                        credentialsId: 'MONDRESSY_US_SHOPIFY_SIGNATURE',
                        variable: 'MONDRESSY_US_SHOPIFY_SIGNATURE'
                    ),
                    string(
                        credentialsId: 'MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT',
                        variable: 'MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT'
                    ),
                    string(
                        credentialsId: 'MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT',
                        variable: 'MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT'
                    )
                ]) {
                    script {
                        if (isUnix()) {
                            env.GRAY_PYTHON_EXIT_CODE = sh(
                                script: '''
                                    .venv/bin/python -u run_all.py \
                                        --site mondressy_US \
                                        --viewport all \
                                        --page all
                                ''',
                                returnStatus: true
                            ).toString()
                        } else {
                            env.GRAY_PYTHON_EXIT_CODE = bat(
                                script: '''
                                    @.venv\\Scripts\\python.exe -u run_all.py ^
                                        --site mondressy_US ^
                                        --viewport all ^
                                        --page all
                                ''',
                                returnStatus: true
                            ).toString()
                        }
                        echo(
                            'Captured GRAY_PYTHON_EXIT_CODE=' +
                            env.GRAY_PYTHON_EXIT_CODE
                        )
                        def capturedCode =
                            env.GRAY_PYTHON_EXIT_CODE?.trim()
                        if (!(capturedCode ==~ /^[0-9]+$/)) {
                            error(
                                'Pipeline state error: invalid ' +
                                'GRAY_PYTHON_EXIT_CODE=' +
                                "${capturedCode}"
                            )
                        }
                    }
                }
            }
        }

        stage('Print Runtime Summary') {
            steps {
                script {
                    def exitCodeFile = '.gray_summary_exit_code'
                    if (isUnix()) {
                        sh """
                            set +e
                            rm -f -- '${exitCodeFile}'

                            .venv/bin/python -u \
                                -m playwright_checks.runtime.gray_summary \
                                --run-id '${env.VISUAL_RUN_ID}' \
                                --python-exit-code '${env.GRAY_PYTHON_EXIT_CODE}'

                            summary_code=\$?
                            printf '%s' "\$summary_code" \
                                > '${exitCodeFile}'
                            exit 0
                        """
                    } else {
                        bat """
                            @echo off
                            if exist "${exitCodeFile}" del /q "${exitCodeFile}"

                            .venv\\Scripts\\python.exe -u ^
                                -m playwright_checks.runtime.gray_summary ^
                                --run-id "${env.VISUAL_RUN_ID}" ^
                                --python-exit-code "${env.GRAY_PYTHON_EXIT_CODE}"

                            set summary_code=%ERRORLEVEL%
                            > "${exitCodeFile}" echo %summary_code%
                            exit /b 0
                        """
                    }

                    def rawSummaryCode = (
                        fileExists(exitCodeFile)
                        ? readFile(file: exitCodeFile).trim()
                        : ''
                    )
                    int normalizedSummaryCode
                    try {
                        normalizedSummaryCode =
                            Integer.parseInt(rawSummaryCode)
                        if (
                            normalizedSummaryCode < 0
                            || normalizedSummaryCode > 255
                        ) {
                            normalizedSummaryCode = 98
                        }
                    } catch (Exception ignored) {
                        normalizedSummaryCode = 98
                    }
                    echo(
                        "Raw summary exit-code file content=" +
                        "'${rawSummaryCode}' length=" +
                        rawSummaryCode.length()
                    )
                    writeFile(
                        file: exitCodeFile,
                        text: normalizedSummaryCode.toString()
                    )
                    echo(
                        'Captured GRAY_SUMMARY_EXIT_CODE=' +
                        normalizedSummaryCode.toString()
                    )
                }
            }
        }

        stage('Archive Artifacts') {
            steps {
                script {
                    try {
                        archiveArtifacts(
                            artifacts: (
                                "artifacts/${env.VISUAL_RUN_ID}/artifact-summary.json," +
                                "artifacts/${env.VISUAL_RUN_ID}/visual-results.json," +
                                "artifacts/${env.VISUAL_RUN_ID}/**/artifact-manifest.json," +
                                "artifacts/${env.VISUAL_RUN_ID}/**/runtime/*.json," +
                                "artifacts/${env.VISUAL_RUN_ID}/**/current/*.png," +
                                "artifacts/${env.VISUAL_RUN_ID}/**/diff/*.png," +
                                "reports/visual-results.json"
                            ),
                            excludes: (
                                "artifacts/${env.VISUAL_RUN_ID}/**/.tmp/**," +
                                "artifacts/${env.VISUAL_RUN_ID}/**/.staging-*," +
                                "**/baselines/**,**/.venv/**,**/__pycache__/**"
                            ),
                            allowEmptyArchive: true,
                            fingerprint: true
                        )
                    } catch (archiveError) {
                        echo(
                            'Gray evidence archive encountered an error; ' +
                            'result evaluation will still continue.'
                        )
                    }
                }
            }
        }

        stage('Clean Current Run Temporary Artifacts') {
            steps {
                script {
                    int cleanupExitCode
                    if (isUnix()) {
                        cleanupExitCode = sh(
                            script: '''
                                .venv/bin/python -u \
                                    -m playwright_checks.artifacts.cleanup \
                                    --keep-run "$VISUAL_RUN_ID" \
                                    --current-run-temp
                            ''',
                            returnStatus: true
                        )
                    } else {
                        cleanupExitCode = bat(
                            script: '''
                                @.venv\\Scripts\\python.exe -u -m playwright_checks.artifacts.cleanup --keep-run "%VISUAL_RUN_ID%" --current-run-temp
                            ''',
                            returnStatus: true
                        )
                    }
                    if (cleanupExitCode != 0) {
                        echo(
                            'Temporary artifact cleanup reported a non-zero ' +
                            'status; result evaluation will continue.'
                        )
                    }
                }
            }
        }

        stage('Evaluate Result') {
            steps {
                script {
                    if (!(env.GRAY_PYTHON_EXIT_CODE ==~ /^[0-9]+$/)) {
                        error(
                            'Pipeline state error: invalid ' +
                            'GRAY_PYTHON_EXIT_CODE=' +
                            env.GRAY_PYTHON_EXIT_CODE
                        )
                    }
                    if (env.GRAY_PYTHON_EXIT_CODE != '0') {
                        error(
                            'Gray validation Python exit code: ' +
                            env.GRAY_PYTHON_EXIT_CODE
                        )
                    }
                    def summaryExitFile = '.gray_summary_exit_code'
                    int summaryExitCode = 98
                    if (fileExists(summaryExitFile)) {
                        def rawSummaryCode = readFile(
                            file: summaryExitFile
                        ).trim()
                        try {
                            summaryExitCode =
                                Integer.parseInt(rawSummaryCode)
                            if (
                                summaryExitCode < 0
                                || summaryExitCode > 255
                            ) {
                                summaryExitCode = 98
                            }
                        } catch (Exception ignored) {
                            summaryExitCode = 98
                        }
                    } else {
                        echo 'Summary exit-code file is missing.'
                    }
                    echo(
                        'Evaluate GRAY_SUMMARY_EXIT_CODE=' +
                        summaryExitCode
                    )
                    if (summaryExitCode != 0) {
                        error(
                            'Runtime gray summary validation failed ' +
                            "(exit code=${summaryExitCode})."
                        )
                    }
                    echo 'Mondressy US Runtime gray validation passed.'
                }
            }
        }
    }

    post {
        always {
            script {
                int exitCodeFileCleanupCode
                if (isUnix()) {
                    exitCodeFileCleanupCode = sh(
                        script: 'rm -f -- .gray_summary_exit_code',
                        returnStatus: true
                    )
                } else {
                    exitCodeFileCleanupCode = bat(
                        script: '''
                            @if exist .gray_summary_exit_code (
                                del /f /q .gray_summary_exit_code
                            )
                            @exit /b 0
                        ''',
                        returnStatus: true
                    )
                }
                if (exitCodeFileCleanupCode != 0) {
                    echo(
                        'Warning: failed to remove summary exit-code file.'
                    )
                }
            }
        }

        success {
            echo 'Mondressy US Runtime gray validation succeeded.'
        }

        failure {
            echo(
                'Mondressy US Runtime gray validation failed. ' +
                'Review the primary error and archived evidence.'
            )
        }
    }
}
