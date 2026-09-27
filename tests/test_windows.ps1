$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).ProviderPath
$cli = Join-Path $root 'scripts/wsl-devctl-win.ps1'
$router = Join-Path $root 'scripts/wsl-devctl.ps1'
$fixture = Join-Path $env:TEMP ("wsl-devctl-win-test-" + [guid]::NewGuid().ToString('N'))
$env:WSL_DEVCTL_WIN_REGISTRY = Join-Path $fixture 'registry.json'
$previousWslEnv = $env:WSLENV
$env:WSLENV = (@($previousWslEnv, 'WSL_DEVCTL_WIN_REGISTRY') | Where-Object { $_ }) -join ':'
$project = Join-Path $fixture 'project'
$name = 'windows-smoke-test'

function Assert($condition, [string]$message) {
    if (-not $condition) { throw $message }
}

try {
    [IO.Directory]::CreateDirectory($project) | Out-Null
    $configuration = @{
        name = $name
        services = @{
            backend = @{
                workdir = '.'
                prepare = "Set-Content -LiteralPath prepared.txt -Value ready"
                run = 'while ($true) { Write-Output alive; Start-Sleep -Seconds 1 }'
            }
        }
    }
    $configuration | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $project 'wsl-devctl.windows.json')
    $linuxProject = '/mnt/' + $project.Substring(0, 1).ToLowerInvariant() +
        $project.Substring(2).Replace('\', '/')
    & wsl.exe -d Ubuntu -- wsl-devctl win register $linuxProject
    Assert ($LASTEXITCODE -eq 0) 'WSL could not register Windows project'
    Assert (Test-Path -LiteralPath (Join-Path $project '.wsl-devctl/windows')) 'runtime directory missing'
    Assert ((& $router help) -match 'Win/WSL') 'unified help omitted both environments'
    Assert ((& $router win list --help) -match 'win list') 'Windows subcommand help failed'
    Assert ((& $router list --help) -match 'combined Win/WSL') 'combined list help failed'
    $listed = @(& $router list --json | ConvertFrom-Json)
    Assert (@($listed | Where-Object { $_.name -eq $name -and $_.environment -eq 'win' }).Count -eq 1) 'router omitted Windows project'
    $wslListed = @(& wsl.exe -d Ubuntu -- wsl-devctl list --json | ConvertFrom-Json)
    Assert (@($wslListed | Where-Object { $_.name -eq $name -and $_.environment -eq 'win' }).Count -eq 1) 'native WSL list omitted Windows project'
    $wslOnly = @(& wsl.exe -d Ubuntu -- wsl-devctl wsl list --json | ConvertFrom-Json)
    Assert (@($wslOnly | Where-Object { $_.name -eq $name }).Count -eq 0) 'explicit WSL list included Windows project'
    $table = & $router list | Out-String
    Assert ($table -match 'Environment' -and $table -match 'Win' -and $table -match $name) 'combined table omitted environment'
    & wsl.exe -d Ubuntu -- wsl-devctl win start $name --prepare
    Assert ($LASTEXITCODE -eq 0) 'WSL could not start Windows service'
    Assert (Test-Path -LiteralPath (Join-Path $project 'prepared.txt')) 'prepare command did not run'
    $status = & $router status $name --json | ConvertFrom-Json
    Assert ($status.services[0].active) 'Windows worker did not start'
    $wslStatus = & wsl.exe -d Ubuntu -- wsl-devctl status $name --json | ConvertFrom-Json
    Assert ($wslStatus.services[0].active) 'native WSL command did not route Windows status'
    Start-Sleep -Seconds 2
    $logs = & $router logs -n 20 $name
    Assert (($logs -join "`n") -match 'alive') "service output not logged: $($logs -join ' | ')"
    & $router restart $name
    $status = & $router status $name --json | ConvertFrom-Json
    Assert ($status.services[0].active) 'Windows worker did not restart'
    & $router stop $name
    $status = & $router status $name --json | ConvertFrom-Json
    Assert (-not $status.services[0].active) 'Windows worker did not stop'
    & wsl.exe -d Ubuntu -- wsl-devctl up $name
    Assert ($LASTEXITCODE -eq 0) 'native WSL command did not route Windows up'
    $status = & $router status $name --json | ConvertFrom-Json
    $lastPid = [int]$status.services[0].pid
    Remove-Item -LiteralPath (Join-Path $project 'wsl-devctl.windows.json')
    & $router unregister $name
    Assert (-not (Get-Process -Id $lastPid -ErrorAction SilentlyContinue)) 'unregister left a worker running'
    $listed = @(& $router list --json | ConvertFrom-Json)
    Assert (@($listed | Where-Object { $_.name -eq $name }).Count -eq 0) 'project remained registered'
    Write-Host 'Windows CLI smoke test passed'
}
finally {
    if (Test-Path -LiteralPath $env:WSL_DEVCTL_WIN_REGISTRY) {
        $remaining = @(& $cli list --json | ConvertFrom-Json)
        if (@($remaining | Where-Object { $_.name -eq $name }).Count) { & $cli stop $name }
    }
    $resolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\', '/')
    $resolvedFixture = [IO.Path]::GetFullPath($fixture)
    if (-not $resolvedFixture.StartsWith("$resolvedTemp\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing cleanup outside temp: $resolvedFixture"
    }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
    Remove-Item Env:WSL_DEVCTL_WIN_REGISTRY -ErrorAction SilentlyContinue
    if ($previousWslEnv) { $env:WSLENV = $previousWslEnv }
    else { Remove-Item Env:WSLENV -ErrorAction SilentlyContinue }
}
