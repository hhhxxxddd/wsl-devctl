param(
    [Parameter(Mandatory = $true)][string]$ProjectPath,
    [Parameter(Mandatory = $true)][string]$Service
)

$ErrorActionPreference = 'Stop'
$stateDirectory = Join-Path $ProjectPath '.wsl-devctl/windows'
$stdout = Join-Path $stateDirectory "$Service.stdout.log"
$stderr = Join-Path $stateDirectory "$Service.stderr.log"
$configPath = Join-Path $ProjectPath 'wsl-devctl.windows.json'
try {
    $config = Get-Content -LiteralPath $configPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
    $definition = $config.services[$Service]
    if (-not $definition -or -not $definition.run) {
        throw "Windows service '$Service' has no run command in $configPath"
    }
    $relativeWorkdir = if ($definition.workdir) { [string]$definition.workdir } else { '.' }
    $workdir = [IO.Path]::GetFullPath((Join-Path $ProjectPath $relativeWorkdir))
    Set-Location -LiteralPath $workdir
    $command = [scriptblock]::Create([string]$definition.run)
}
catch {
    Add-Content -LiteralPath $stderr -Value "[$(Get-Date -Format o)] $Service could not start: $_"
    throw
}

while ($true) {
    try {
        $global:LASTEXITCODE = 0
        & $command 1>> $stdout 2>> $stderr
        $code = $global:LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        Add-Content -LiteralPath $stderr -Value "[$(Get-Date -Format o)] $Service exited with code $code; restarting in 3 seconds"
    }
    catch {
        Add-Content -LiteralPath $stderr -Value "[$(Get-Date -Format o)] $Service failed: $_; restarting in 3 seconds"
    }
    Start-Sleep -Seconds 3
}
