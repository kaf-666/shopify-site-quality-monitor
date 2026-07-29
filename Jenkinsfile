pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
    }

    environment {
        DIAGNOSTIC_RUN_ID = "jenkins-${BUILD_NUMBER}-mondressy-429-diagnostic"
        PLAYWRIGHT_BROWSER_CHANNEL = 'chromium'
        RUNTIME_HEALTH_ENABLED = 'true'
        RUNTIME_HEALTH_REPORT_ONLY = 'true'
        RUNTIME_HEALTH_AFFECT_EXIT_CODE = 'false'
        VISUAL_STRICT_WARNINGS = 'true'
        ALLOW_BASELINE_INIT = 'false'
        FORCE_BASELINE_INIT = 'false'
        ALLOW_SIDE_EFFECT_FLOW = 'false'
        DIAGNOSTIC_EXIT_CODE = 'not_run'
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
                            'This diagnostic only permits a manual ' +
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

                    echo 'diagnostic_scope=mondressy_429_six_probes'
                    echo 'probe_order=curl,APIRequest,Chromium'
                    echo 'hosts=mondressy.com,www.mondressy.com'
                    echo 'request_header_injection=extra_http_headers'
                    echo 'chromium_route_injection=false'
                    echo 'visual_checks=false'
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

        stage('Run Mondressy 429 Six-Probe Diagnostic') {
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
                                    .venv/bin/python -u \
                                        -m playwright_checks.diagnostics.mondressy_429 \
                                        --output "artifacts/$DIAGNOSTIC_RUN_ID/mondressy-429-diagnostic.json"
                                '''
                            )
                        } else {
                            exitCode = bat(
                                returnStatus: true,
                                script: '''
                                    @.venv\\Scripts\\python.exe -u -m playwright_checks.diagnostics.mondressy_429 --output "artifacts\\%DIAGNOSTIC_RUN_ID%\\mondressy-429-diagnostic.json"
                                '''
                            )
                        }
                        env.DIAGNOSTIC_EXIT_CODE = "${exitCode}"
                    }
                }
            }
        }

        stage('Evaluate Diagnostic Execution') {
            steps {
                script {
                    if (env.DIAGNOSTIC_EXIT_CODE == 'not_run') {
                        error('Mondressy 429 diagnostic process did not run.')
                    }
                    if (env.DIAGNOSTIC_EXIT_CODE != '0') {
                        error(
                            'Mondressy 429 diagnostic execution failed: ' +
                            env.DIAGNOSTIC_EXIT_CODE
                        )
                    }
                }
            }
        }
    }

    post {
        always {
            script {
                try {
                    archiveArtifacts(
                        artifacts: "artifacts/${env.DIAGNOSTIC_RUN_ID}/**",
                        allowEmptyArchive: true,
                        fingerprint: true
                    )
                } catch (archiveError) {
                    echo(
                        'Diagnostic artifact archive encountered an error; ' +
                        'the primary build result is preserved.'
                    )
                }
            }
        }

        success {
            echo 'Mondressy 429 six-probe diagnostic completed.'
        }

        failure {
            echo(
                'Mondressy 429 diagnostic did not complete cleanly. ' +
                'Review the redacted probe output and archived report.'
            )
        }
    }
}
