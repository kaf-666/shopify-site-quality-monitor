pipeline {
    agent any

    options {
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
        VISUAL_STRICT_WARNINGS = 'true'
        ALLOW_BASELINE_INIT = 'false'
        FORCE_BASELINE_INIT = 'false'
        ALLOW_SIDE_EFFECT_FLOW = 'false'
        GRAY_PYTHON_EXIT_CODE = 'not_run'
        GRAY_SUMMARY_EXIT_CODE = 'not_run'
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
                        int exitCode
                        if (isUnix()) {
                            exitCode = sh(
                                returnStatus: true,
                                script: '''
                                    .venv/bin/python -u run_all.py \
                                        --site mondressy_US \
                                        --viewport desktop \
                                        --page home
                                '''
                            )
                        } else {
                            exitCode = bat(
                                returnStatus: true,
                                script: '''
                                    @.venv\\Scripts\\python.exe -u run_all.py --site mondressy_US --viewport desktop --page home
                                '''
                            )
                        }
                        env.GRAY_PYTHON_EXIT_CODE = "${exitCode}"
                    }
                }
            }
        }

        stage('Print Runtime Summary') {
            steps {
                script {
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
                    env.GRAY_SUMMARY_EXIT_CODE = "${summaryExitCode}"
                }
            }
        }

        stage('Archive Artifacts') {
            steps {
                script {
                    try {
                        archiveArtifacts(
                            artifacts: "artifacts/${env.VISUAL_RUN_ID}/**,reports/visual-results.json",
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

        stage('Evaluate Result') {
            steps {
                script {
                    if (env.GRAY_PYTHON_EXIT_CODE == 'not_run') {
                        error('Gray validation Python process did not run.')
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
