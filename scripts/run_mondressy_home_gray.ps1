[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$credentialNames = @(
    'MONDRESSY_US_SHOPIFY_SIGNATURE'
    'MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT'
    'MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT'
)

$missingCredentialNames = @()
foreach ($credentialName in $credentialNames) {
    $loaded = -not [string]::IsNullOrWhiteSpace(
        [Environment]::GetEnvironmentVariable($credentialName, 'Process')
    )
    Write-Host "$credentialName loaded=$loaded"

    if (-not $loaded) {
        $missingCredentialNames += $credentialName
    }
}

if ($missingCredentialNames.Count -gt 0) {
    [Console]::Error.WriteLine(
        'ERROR: ' +
        'Missing required environment variables: ' +
        ($missingCredentialNames -join ', ') +
        '. Load the external secrets file before running this script.'
    )
    exit 2
}

$env:PLAYWRIGHT_BROWSER_CHANNEL = 'chromium'
$env:PLAYWRIGHT_HEADED = '0'
$env:RUNTIME_HEALTH_ENABLED = '1'
$env:RUNTIME_HEALTH_REPORT_ONLY = '1'
$env:RUNTIME_HEALTH_AFFECT_EXIT_CODE = '0'
$env:VISUAL_STRICT_WARNINGS = '0'
$env:ALLOW_BASELINE_INIT = '0'
$env:FORCE_BASELINE_INIT = '0'

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "ERROR: Python virtual environment executable not found: $pythonPath"
    )
    exit 3
}

$pythonExitCode = 0
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath run_all.py `
        --validate-config `
        --site mondressy_US `
        --viewport all
    $pythonExitCode = $LASTEXITCODE

    if ($pythonExitCode -eq 0) {
        & $pythonPath run_all.py `
            --site mondressy_US `
            --viewport all `
            --page home
        $pythonExitCode = $LASTEXITCODE
    }
}
finally {
    Pop-Location
}

exit $pythonExitCode
