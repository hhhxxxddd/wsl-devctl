# Windows-native development runner. Project commands run in the source tree;
# only local process state and logs are written to .wsl-devctl/windows/.
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'Windows mode requires PowerShell 7 or newer (pwsh).'
}

$configFileName = 'wsl-devctl.windows.json'
$namePattern = '^[a-z0-9][a-z0-9._-]{0,62}$'

function Get-RegistryPath {
    if ($env:WSL_DEVCTL_WIN_REGISTRY) {
        return [IO.Path]::GetFullPath($env:WSL_DEVCTL_WIN_REGISTRY)
    }
    return Join-Path $env:LOCALAPPDATA 'wsl-devctl/registry.json'
}

function Read-Registry {
    $path = Get-RegistryPath
    if (-not (Test-Path -LiteralPath $path)) { return @{} }
    $value = Get-Content -LiteralPath $path -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
    if ($value -isnot [System.Collections.IDictionary]) { throw "Invalid Windows registry: $path" }
    return $value
}

function Save-Registry([System.Collections.IDictionary]$registry) {
    $path = Get-RegistryPath
    $directory = Split-Path -Parent $path
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    $temporary = "$path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        $json = ConvertTo-Json -InputObject $registry -Depth 8
        [IO.File]::WriteAllText($temporary, $json, [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $path -Force
    }
    finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
}

function Get-ProjectPath([string]$name) {
    $registry = Read-Registry
    if (-not $registry.Contains($name)) { throw "Windows project is not registered: $name" }
    $path = [string]$registry[$name]
    if (-not (Test-Path -LiteralPath $path -PathType Container)) {
        throw "Windows project directory is missing: $path"
    }
    return (Resolve-Path -LiteralPath $path).ProviderPath
}

function Get-Config([string]$projectPath) {
    $path = Join-Path $projectPath $configFileName
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing $configFileName in $projectPath"
    }
    $config = Get-Content -LiteralPath $path -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
    if ($config -isnot [System.Collections.IDictionary] -or [string]$config.name -cnotmatch $namePattern) {
        throw "Invalid project name in $path"
    }
    if ($config.services -isnot [System.Collections.IDictionary] -or $config.services.Count -eq 0) {
        throw "Configure at least one service in $path"
    }
    foreach ($service in $config.services.Keys) {
        if ([string]$service -cnotmatch $namePattern) { throw "Invalid service name: $service" }
        $item = $config.services[$service]
        if ($item -isnot [System.Collections.IDictionary] -or -not ([string]$item.run).Trim()) {
            throw "Service '$service' needs a run command"
        }
        $null = Get-Workdir $projectPath $item
        if ($item.Contains('port') -and ($item.port -isnot [int] -or $item.port -lt 1 -or $item.port -gt 65535)) {
            throw "Invalid port for service '$service'"
        }
    }
    return $config
}

function Get-Workdir([string]$projectPath, [System.Collections.IDictionary]$item) {
    $relative = if ($item.workdir) { [string]$item.workdir } else { '.' }
    if ([IO.Path]::IsPathRooted($relative)) { throw "Service workdir must be relative: $relative" }
    $root = [IO.Path]::GetFullPath($projectPath).TrimEnd('\', '/')
    $workdir = [IO.Path]::GetFullPath((Join-Path $root $relative))
    if ($workdir -ne $root -and -not $workdir.StartsWith("$root\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Service workdir escapes project: $relative"
    }
    if (-not (Test-Path -LiteralPath $workdir -PathType Container)) {
        throw "Service workdir is missing: $workdir"
    }
    return $workdir
}

function Get-StateDir([string]$projectPath) {
    $base = Join-Path $projectPath '.wsl-devctl'
    $directory = Join-Path $base 'windows'
    foreach ($path in @($base, $directory)) {
        if (Test-Path -LiteralPath $path) {
            if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing linked runtime directory: $path"
            }
            if (-not (Test-Path -LiteralPath $path -PathType Container)) {
                throw "Runtime path is not a directory: $path"
            }
        }
    }
    return $directory
}

function Ensure-StateDir([string]$projectPath) {
    $directory = Get-StateDir $projectPath
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    return $directory
}

function Get-StatePath([string]$projectPath, [string]$service) {
    return Join-Path (Get-StateDir $projectPath) "$service.json"
}

function Get-RunningProcess([string]$projectPath, [string]$service) {
    $path = Get-StatePath $projectPath $service
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try {
        $record = Get-Content -LiteralPath $path -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
        $process = Get-Process -Id ([int]$record.pid) -ErrorAction Stop
        if ($process.StartTime.ToUniversalTime().Ticks -eq [long]$record.start_ticks) {
            return $process
        }
    }
    catch { return $null }
    return $null
}

function Test-Port([int]$port) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $attempt = $client.ConnectAsync('127.0.0.1', $port)
        return $attempt.Wait(350) -and $client.Connected
    }
    catch { return $false }
    finally { $client.Dispose() }
}

function Get-ServiceStatus([string]$projectPath, [string]$service, [System.Collections.IDictionary]$item) {
    $process = Get-RunningProcess $projectPath $service
    $port = if ($item.Contains('port')) { [int]$item.port } else { $null }
    $reachable = if ($null -ne $port) { Test-Port $port } else { $null }
    return [pscustomobject]@{
        service = $service
        active = ($null -ne $process)
        pid = if ($process) { $process.Id } else { $null }
        port = $port
        reachable = $reachable
        healthy = (($null -ne $process) -and ($null -eq $port -or $reachable))
    }
}

function Invoke-Prepare([string]$projectPath, [System.Collections.IDictionary]$item) {
    $prepare = ([string]$item.prepare).Trim()
    if (-not $prepare) { return }
    $workdir = Get-Workdir $projectPath $item
    Push-Location -LiteralPath $workdir
    try {
        Write-Host "Preparing in $workdir`: $prepare"
        $global:LASTEXITCODE = 0
        & ([scriptblock]::Create($prepare))
        if ($global:LASTEXITCODE -ne 0) { throw "Prepare failed with exit code $global:LASTEXITCODE" }
    }
    finally { Pop-Location }
}

function Start-Service([string]$projectPath, [string]$service, [System.Collections.IDictionary]$item) {
    if (Get-RunningProcess $projectPath $service) {
        Write-Host "$service already running"
        return
    }
    $directory = Ensure-StateDir $projectPath
    $workdir = Get-Workdir $projectPath $item
    $worker = Join-Path $PSScriptRoot 'wsl-devctl-win-worker.ps1'
    foreach ($stream in @('stdout', 'stderr')) {
        [IO.File]::WriteAllText((Join-Path $directory "$service.$stream.log"), '')
    }
    # WMI creates the worker outside the WSL interop process tree. Otherwise an
    # invocation from WSL waits for the long-running Windows child to exit.
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $commandLine = '"' + (Join-Path $PSHOME 'pwsh.exe') + '" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
        $worker + '" -ProjectPath "' + $projectPath + '" -Service ' + $service
    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $commandLine
        CurrentDirectory = $workdir
        ProcessStartupInformation = $startup
    }
    if ($result.ReturnValue -ne 0) { throw "Could not launch $service (WMI code $($result.ReturnValue))" }
    $process = Get-Process -Id ([int]$result.ProcessId) -ErrorAction Stop
    $record = @{ pid = $process.Id; start_ticks = $process.StartTime.ToUniversalTime().Ticks }
    [IO.File]::WriteAllText((Get-StatePath $projectPath $service),
        (ConvertTo-Json -InputObject $record), [Text.UTF8Encoding]::new($false))
    Start-Sleep -Milliseconds 400
    if (-not (Get-RunningProcess $projectPath $service)) {
        throw "$service worker exited during startup; see .wsl-devctl/windows/$service.stderr.log"
    }
    Write-Host "Started $service (PID $($process.Id))"
}

function Stop-Service([string]$projectPath, [string]$service) {
    $process = Get-RunningProcess $projectPath $service
    if ($process) {
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-RunningProcess $projectPath $service)) {
            throw "Could not stop $service (PID $($process.Id))"
        }
        Write-Host "Stopped $service"
    }
    else { Write-Host "$service already stopped" }
    Remove-Item -LiteralPath (Get-StatePath $projectPath $service) -Force -ErrorAction SilentlyContinue
}

function Get-KnownServices([string]$projectPath, [string[]]$configured) {
    $directory = Get-StateDir $projectPath
    $names = @($configured)
    if (Test-Path -LiteralPath $directory) {
        $names += @(Get-ChildItem -LiteralPath $directory -Filter '*.json' -File |
            ForEach-Object { $_.BaseName } | Where-Object { $_ -cmatch $namePattern })
    }
    return @($names | Sort-Object -Unique)
}

function Add-LocalGitExclude([string]$projectPath) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return }
    $top = (& git -C $projectPath rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $top) { return }
    $topPath = [IO.Path]::GetFullPath(([string]$top).Trim())
    if (-not [string]::Equals($topPath, $projectPath, [StringComparison]::OrdinalIgnoreCase)) { return }
    $exclude = (& git -C $projectPath rev-parse --git-path info/exclude 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $exclude) { return }
    $excludePath = [string]$exclude
    if (-not [IO.Path]::IsPathRooted($excludePath)) { $excludePath = Join-Path $projectPath $excludePath }
    [IO.Directory]::CreateDirectory((Split-Path -Parent $excludePath)) | Out-Null
    $existing = if (Test-Path -LiteralPath $excludePath) { Get-Content -LiteralPath $excludePath } else { @() }
    if ($existing -notcontains '/.wsl-devctl/') {
        Add-Content -LiteralPath $excludePath -Value '/.wsl-devctl/' -Encoding utf8
    }
}

function Register-Project([string]$requestedPath) {
    $projectPath = (Resolve-Path -LiteralPath $requestedPath -ErrorAction Stop).ProviderPath
    $config = Get-Config $projectPath
    $registry = Read-Registry
    $name = [string]$config.name
    if ($registry.Contains($name) -and -not [string]::Equals([string]$registry[$name], $projectPath,
        [StringComparison]::OrdinalIgnoreCase)) {
        throw "Windows project '$name' already points to $($registry[$name])"
    }
    $null = Ensure-StateDir $projectPath
    Add-LocalGitExclude $projectPath
    $registry[$name] = $projectPath
    Save-Registry $registry
    Write-Host "Registered Windows project $name -> $projectPath"
}

function Get-ProjectReport([string]$name, [string]$projectPath) {
    try {
        $config = Get-Config $projectPath
        if ([string]$config.name -cne $name) { throw 'Registered name differs from project config' }
        $services = @($config.services.Keys | Sort-Object | ForEach-Object {
            Get-ServiceStatus $projectPath ([string]$_) $config.services[$_]
        })
        $running = @($services | Where-Object { $_.active } | ForEach-Object { $_.service })
        return [pscustomobject]@{
            name = $name; environment = 'win'; state = $running
            valid = $true; path = $projectPath; services = $services
            healthy = ($services.Count -gt 0 -and @($services | Where-Object { -not $_.healthy }).Count -eq 0)
        }
    }
    catch {
        return [pscustomobject]@{
            name = $name; environment = 'win'; state = @(); valid = $false
            path = $projectPath; services = @(); healthy = $false; error = [string]$_
        }
    }
}

function Write-Json($value) { ConvertTo-Json -InputObject $value -Depth 12 }

function Show-Help([string]$action = '') {
    $usages = @{
        register = 'wsl-devctl win register <project-directory>'
        unregister = 'wsl-devctl win unregister <name>'
        list = 'wsl-devctl win list [--json]'
        start = 'wsl-devctl win start <name> [--prepare]'
        stop = 'wsl-devctl win stop <name>'
        restart = 'wsl-devctl win restart <name>'
        prepare = 'wsl-devctl win prepare <name>'
        status = 'wsl-devctl win status <name> [--json]'
        logs = 'wsl-devctl win logs <name> [-n 100] [-f]'
        show = 'wsl-devctl win show <name> [--json]'
    }
    if ($action) {
        if (-not $usages.ContainsKey($action)) { throw "Unknown Windows command: $action" }
        Write-Output "Usage: $($usages[$action])"
        return
    }
    @'
Windows-native development services (PowerShell 7):
  wsl-devctl win register <project-directory>  Register wsl-devctl.windows.json
  wsl-devctl win unregister <name>           Stop and unregister; keep logs
  wsl-devctl win list [--json]               List Windows projects
  wsl-devctl win start <name> [--prepare]    Start native services
  wsl-devctl win stop <name>                 Stop native services
  wsl-devctl win restart <name>              Restart native services
  wsl-devctl win prepare <name>              Run configured prepare commands
  wsl-devctl win status <name> [--json]      Show process and port health
  wsl-devctl win logs <name> [-n 100] [-f]  Read native service logs
  wsl-devctl win show <name> [--json]        Show project configuration

Source and builds remain in the project. Logs/state use .wsl-devctl/windows/.
'@ | Write-Output
}

try {
    if ($args.Count -eq 0 -or $args[0] -in @('help', '--help', '-h')) { Show-Help; return }
    $action = [string]$args[0]
    if ($args -contains '--help' -or $args -contains '-h') { Show-Help $action; return }
    $allowed = @{
        register = @(); unregister = @(); list = @('--json')
        start = @('--prepare'); stop = @(); restart = @(); prepare = @()
        status = @('--json'); show = @('--json'); logs = @('-n', '--lines', '-f', '--follow')
    }
    if (-not $allowed.ContainsKey($action)) { throw "Unknown Windows command: $action" }
    $positionals = [Collections.Generic.List[string]]::new()
    $options = [Collections.Generic.List[string]]::new()
    $tail = 100
    for ($i = 1; $i -lt $args.Count; $i++) {
        $token = [string]$args[$i]
        if ($token -notin $allowed[$action] -and $token.StartsWith('-')) {
            throw "Unknown option for $action`: $token"
        }
        if ($token -in @('-n', '--lines')) {
            if ($i + 1 -ge $args.Count -or -not [int]::TryParse([string]$args[++$i], [ref]$tail) -or $tail -lt 1) {
                throw 'Logs line count must be a positive integer'
            }
        }
        elseif ($token -in $allowed[$action]) { $options.Add($token) }
        else { $positionals.Add($token) }
    }
    $expected = if ($action -eq 'list') { 0 } else { 1 }
    if ($positionals.Count -ne $expected) { Show-Help $action; throw "Expected $expected argument(s) for $action" }
    if ($action -eq 'register') {
        Register-Project $positionals[0]; return
    }
    if ($action -eq 'list') {
        $registry = Read-Registry
        $reports = @($registry.Keys | Sort-Object | ForEach-Object {
            Get-ProjectReport ([string]$_) ([string]$registry[$_])
        })
        if ($options.Contains('--json')) { Write-Json $reports; return }
        $reports | Select-Object Name, Environment, @{n='State';e={
            if (-not $_.valid) { 'INVALID' } elseif ($_.state.Count) { $_.state -join ',' } else { 'stopped' }
        }} | Format-Table -AutoSize
        return
    }
    $name = $positionals[0]
    if ($action -eq 'unregister') {
        $registry = Read-Registry
        if (-not $registry.Contains($name)) { throw "Windows project is not registered: $name" }
        $registeredPath = [string]$registry[$name]
        if (Test-Path -LiteralPath $registeredPath -PathType Container) {
            foreach ($service in @(Get-KnownServices $registeredPath @())) {
                Stop-Service $registeredPath $service
            }
        }
        $registry.Remove($name)
        Save-Registry $registry
        Write-Host "Unregistered Windows project $name; local logs retained"
        return
    }
    $projectPath = Get-ProjectPath $name
    $config = Get-Config $projectPath
    if ([string]$config.name -cne $name) { throw 'Registered name differs from project config' }
    $services = @($config.services.Keys | Sort-Object)
    $knownServices = @(Get-KnownServices $projectPath $services)
    switch ($action) {
        'show' {
            if (-not $options.Contains('--json')) { Write-Host "Project: $projectPath" }
            Write-Json $config
        }
        'status' {
            $report = Get-ProjectReport $name $projectPath
            if ($options.Contains('--json')) { Write-Json $report }
            else {
                $report.services | Select-Object Service, Active, Pid, Port, Reachable, Healthy |
                    Format-Table -AutoSize
                Write-Host "Runtime: $(Join-Path $projectPath '.wsl-devctl/windows')"
            }
        }
        'prepare' {
            foreach ($service in $knownServices) { Stop-Service $projectPath $service }
            foreach ($service in $services) { Invoke-Prepare $projectPath $config.services[$service] }
        }
        'start' {
            if ($options.Contains('--prepare')) {
                foreach ($service in $knownServices) { Stop-Service $projectPath $service }
                foreach ($service in $services) { Invoke-Prepare $projectPath $config.services[$service] }
            }
            foreach ($service in $services) { Start-Service $projectPath $service $config.services[$service] }
        }
        'stop' {
            foreach ($service in $knownServices) { Stop-Service $projectPath $service }
        }
        'restart' {
            foreach ($service in $knownServices) { Stop-Service $projectPath $service }
            foreach ($service in $services) { Start-Service $projectPath $service $config.services[$service] }
        }
        'logs' {
            $directory = Get-StateDir $projectPath
            $files = @($services | ForEach-Object {
                @((Join-Path $directory "$_.stdout.log"), (Join-Path $directory "$_.stderr.log"))
            } | Where-Object { Test-Path -LiteralPath $_ })
            if (-not $files.Count) { Write-Host 'No Windows service logs yet'; return }
            if ($options.Contains('-f') -or $options.Contains('--follow')) { Get-Content -LiteralPath $files -Tail $tail -Wait }
            else { foreach ($file in $files) { Write-Host "== $file =="; Get-Content -LiteralPath $file -Tail $tail } }
        }
        default { throw "Unknown Windows command: $action" }
    }
}
catch {
    Write-Error $_
    throw
}
