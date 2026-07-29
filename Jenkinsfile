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
        GRAY_PYTHON_EXIT_CODE = 'not_run'
        GRAY_SUMMARY_EXIT_CODE = 'not_run'
        GRAY_PIPELINE_STATE_ERROR = 'false'
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
                    echo 'page=Home'
                    echo 'viewport=desktop'
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
                        int pythonExitCode
                        if (isUnix()) {
                            pythonExitCode = sh(
                                script: '''
                                    .venv/bin/python -u run_all.py \
                                        --site mondressy_US \
                                        --viewport desktop \
                                        --page home
                                ''',
                                returnStatus: true
                            )
                        } else {
                            pythonExitCode = bat(
                                script: '''
                                    @.venv\\Scripts\\python.exe -u run_all.py --site mondressy_US --viewport desktop --page home
                                ''',
                                returnStatus: true
                            )
                        }
                        env.GRAY_PYTHON_EXIT_CODE =
                            String.valueOf(pythonExitCode)
                        echo(
                            'Captured GRAY_PYTHON_EXIT_CODE=' +
                            env.GRAY_PYTHON_EXIT_CODE
                        )
                    }
                }
            }
        }

        stage('Print Runtime Summary') {
            steps {
                script {
                    if (
                        !(env.GRAY_PYTHON_EXIT_CODE ==~ /^[0-9]+$/)
                    ) {
                        env.GRAY_PIPELINE_STATE_ERROR = 'true'
                        env.GRAY_SUMMARY_EXIT_CODE = '2'
                        echo(
                            'Pipeline state error: Python command output ' +
                            'was produced, but its exit code was not captured.'
                        )
                    } else {
                        int summaryExitCode
                        if (isUnix()) {
                            summaryExitCode = sh(
                                returnStatus: true,
                                script: '''
                                    .venv/bin/python -u \
                                        -m playwright_checks.runtime.gray_summary \
                                        --run-id "$VISUAL_RUN_ID" \
                                        --python-exit-code "$GRAY_PYTHON_EXIT_CODE"
                                '''
                            )
                        } else {
                            summaryExitCode = bat(
                                returnStatus: true,
                                script: '''
                                    @.venv\\Scripts\\python.exe -u -m playwright_checks.runtime.gray_summary --run-id "%VISUAL_RUN_ID%" --python-exit-code "%GRAY_PYTHON_EXIT_CODE%"
                                '''
                            )
                        }
                        env.GRAY_SUMMARY_EXIT_CODE =
                            String.valueOf(summaryExitCode)
                    }
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
                    if (
                        env.GRAY_PIPELINE_STATE_ERROR == 'true'
                        || !(env.GRAY_PYTHON_EXIT_CODE ==~ /^[0-9]+$/)
                    ) {
                        error(
                            'Pipeline state error: Python command output ' +
                            'was produced, but its exit code was not captured.'
                        )
                    }
                    if (env.GRAY_PYTHON_EXIT_CODE != '0') {
                        error(
                            'Gray validation Python exit code: ' +
                            env.GRAY_PYTHON_EXIT_CODE
                        )
                    }
                    if (env.GRAY_SUMMARY_EXIT_CODE != '0') {
                        error('Runtime gray summary validation failed.')
                    }
                }
            }
        }
    }

    post {
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
