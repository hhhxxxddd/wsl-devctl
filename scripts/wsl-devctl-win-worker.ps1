param(
    [Parameter(Mandatory = $true)][string]$ProjectPath,
    [Parameter(Mandatory = $true)][string]$Service
)

$ErrorActionPreference = 'Stop'
$stateDirectory = Join-Path $ProjectPath '.wsl-devctl/windows'
$stdout = Join-Path $stateDirectory "$Service.stdout.log"
$stderr = Join-Path $stateDirectory "$Service.stderr.log"
$configPath = Join-Path $ProjectPath 'wsl-devctl.windows.json'
$runtimePath = Join-Path $stateDirectory "$Service.runtime"
$workerTicks = (Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks
function Write-Runtime([string]$phase, $exitCode = $null, [string]$errorText = '') {
    $record = @{ pid = $PID; start_ticks = $workerTicks; phase = $phase
        exit_code = $exitCode; error = $errorText; updated_at = (Get-Date -Format o) }
    $temporary = "$runtimePath.$PID.tmp"
    [IO.File]::WriteAllText($temporary, ($record | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $runtimePath -Force
}
Write-Runtime 'starting'
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
    Write-Runtime 'failed' 1 ([string]$_)
    Add-Content -LiteralPath $stderr -Value "[$(Get-Date -Format o)] $Service could not start: $_"
    throw
}

while ($true) {
    try {
        $global:LASTEXITCODE = 0
        Write-Runtime 'running'
        & $command 2>> $stderr | ForEach-Object { Add-Content -LiteralPath $stdout -Value $_ }
        $code = $global:LASTEXITCODE
        if ($null -eq $code) { $code = 0 }
        Write-Runtime 'restarting' $code
        Add-Content -LiteralPath $stderr -Value "[$(Get-Date -Format o)] $Service exited with code $code; restarting in 3 seconds"
    }
    catch {
        Write-Runtime 'restarting' 1 ([string]$_)
        Add-Content -LiteralPath $stderr -Value "[$(Get-Date -Format o)] $Service failed: $_; restarting in 3 seconds"
    }
    Start-Sleep -Seconds 3
}
