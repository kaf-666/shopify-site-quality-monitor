pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Verify Repository') {
            steps {
                script {
                    if (isUnix()) {
                        sh '''
                            echo "Current workspace:"
                            pwd

                            echo "Latest commit:"
                            git log -1 --oneline

                            echo "Repository files:"
                            ls -la
                        '''
                    } else {
                        bat '''
                            echo Current workspace:
                            cd

                            echo Latest commit:
                            git log -1 --oneline

                            echo Repository files:
                            dir
                        '''
                    }
                }
            }
        }
    }

    post {
        success {
            echo 'GitHub repository checkout succeeded.'
        }

        failure {
            echo 'Pipeline failed. Check the console output.'
        }
    }
}
