# Unified Windows entry point. Existing WSL commands remain the default.
$ErrorActionPreference = 'Stop'
$win = Join-Path $PSScriptRoot 'wsl-devctl-win.ps1'
$distro = if ($env:WSL_DEVCTL_DISTRO) { $env:WSL_DEVCTL_DISTRO } else { 'Ubuntu' }

function Start-WslKeepAlive {
    $script = Join-Path $PSScriptRoot 'wsl-devctl-keepalive.sh'
    $linuxScript = & wsl.exe -d $distro --exec wslpath -u $script.Replace('\', '/')
    if ($LASTEXITCODE -ne 0) { throw 'Could not locate WSL keepalive script' }
    $wrapper = Join-Path $PSScriptRoot 'wsl-devctl-keepalive.ps1'
    $arguments = '-NoProfile -NonInteractive -File "' + $wrapper + '" -Distro "' + $distro +
        '" -LinuxScript "' + ([string]$linuxScript).Trim() + '"'
    $existing = @(Get-CimInstance Win32_Process -Filter "Name = 'pwsh.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($arguments) })
    if (-not $existing.Count) {
        # Detach from short-lived terminal/agent process trees, like Windows workers.
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
        $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = '"' + (Join-Path $PSHOME 'pwsh.exe') + '" ' + $arguments
            ProcessStartupInformation = $startup
        }
        if ($result.ReturnValue -ne 0) { throw "Could not keep WSL running (WMI code $($result.ReturnValue))" }
    }
}

function Get-WslProjects {
    $raw = & wsl.exe -d $distro -- wsl-devctl wsl list --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not list WSL projects' }
    return ,@($raw | ConvertFrom-Json | Where-Object { $_.environment -eq 'wsl' })
}

function Get-WinProjects {
    $raw = & $win list --json
    if ($LASTEXITCODE -ne 0) { throw 'Could not list Windows projects' }
    return ,@($raw | ConvertFrom-Json)
}

function Show-UnifiedHelp {
    @'
wsl-devctl - 管理 WSL 与 Windows 原生开发服务（PowerShell 7 入口）

选择运行环境（每次命令指定，不是持久设置）：
  wsl-devctl win start <名称>             在 Windows 原生环境启动
  wsl-devctl wsl start <名称>             在 WSL 环境启动
  wsl-devctl start <名称>                 按项目注册环境自动选择

查看项目：
  wsl-devctl list [--json]                合并列出 Win/WSL 项目
  wsl-devctl win list                     只列 Windows 项目
  wsl-devctl wsl list                     只列 WSL 项目

其他命令：
  wsl-devctl win register <项目路径>      注册 Windows 项目
  wsl-devctl status|stop|restart <名称>   状态、停止、重启
  wsl-devctl logs <名称> -f               跟踪日志
  wsl-devctl help win                     只查看 Windows 命令说明
  wsl-devctl help wsl                     只查看 WSL 命令说明
  wsl-devctl <命令> --help                查看命令参数

同名项目默认选择 WSL；Windows 项目可用 win 前缀明确指定。
WSL 的 init、register、sync、compile、doctor 等命令仍按原方式使用。
'@ | Write-Output
}

function Get-TargetName([string[]]$arguments) {
    $skipNext = $false
    for ($i = 1; $i -lt $arguments.Count; $i++) {
        $value = [string]$arguments[$i]
        if ($skipNext) { $skipNext = $false; continue }
        if ($value -in @('-n', '--lines')) { $skipNext = $true; continue }
        if ($value.StartsWith('-')) { continue }
        return $value
    }
    return ''
}

if ($args.Count -eq 0 -or $args[0] -in @('help', '--help', '-h')) {
    if ($args.Count -ge 2 -and $args[1] -eq 'win') { & $win help; return }
    if ($args.Count -ge 2 -and $args[1] -eq 'wsl') { & wsl.exe -d $distro -- wsl-devctl help; return }
    if ($args.Count -ge 2 -and $args[1] -notin @('--help', '-h')) {
        $command = [string]$args[1]
        if ($command -eq 'list') { Write-Output 'Usage: wsl-devctl list [--json]  (combined Win/WSL projects)'; return }
        & wsl.exe -d $distro -- wsl-devctl $command --help
        if ($command -in @('start', 'stop', 'restart', 'status', 'show', 'logs', 'prepare', 'unregister')) {
            & $win $command --help
        }
        return
    }
    Show-UnifiedHelp
    return
}

if ($args.Count -gt 0 -and $args[0] -eq 'win') {
    $forward = @($args | Select-Object -Skip 1)
    & $win @forward
    return
}

if ($args[0] -eq 'wsl') {
    if ($args.Count -gt 1 -and $args[1] -in @('start', 'up', 'restart') -and
        $args -notcontains '--help' -and $args -notcontains '-h') { Start-WslKeepAlive }
    & wsl.exe -d $distro -- wsl-devctl @args
    $resultCode = $LASTEXITCODE
    if ($resultCode -eq 0 -and $args.Count -gt 1 -and $args[1] -in @('start', 'up', 'restart') -and
        $args -notcontains '--help' -and $args -notcontains '-h') { Start-WslKeepAlive }
    exit $resultCode
}

if ($args -contains '--help' -or $args -contains '-h') {
    $command = [string]$args[0]
    if ($command -eq 'list') { Write-Output 'Usage: wsl-devctl list [--json]  (combined Win/WSL projects)'; return }
    & wsl.exe -d $distro -- wsl-devctl $command --help
    if ($command -in @('start', 'stop', 'restart', 'status', 'show', 'logs', 'prepare', 'unregister')) {
        & $win $command --help
    }
    return
}

if ($args.Count -gt 0 -and $args[0] -eq 'list') {
    if (@($args | Where-Object { $_ -ne 'list' -and $_ -ne '--json' }).Count) {
        throw 'Usage: wsl-devctl list [--json]'
    }
    $rows = @()
    foreach ($project in (Get-WslProjects)) {
        if ($null -eq $project) { continue }
        $rows += [pscustomobject]@{
            name = $project.name
            environment = 'wsl'
            state = @($project.state)
            valid = $project.valid
            runtime = $project.runtime
            toolchain = $project.toolchain
        }
    }
    foreach ($project in (Get-WinProjects)) {
        if ($null -eq $project) { continue }
        $rows += $project
    }
    if ($args -contains '--json') {
        ConvertTo-Json -InputObject @($rows) -Depth 12
    } else {
        $rows | Sort-Object name, environment |
            Select-Object @{n='Name';e={$_.name}},
                @{n='Environment';e={if ($_.environment -eq 'win') {'Win'} else {'WSL'}}},
                @{n='State';e={if (-not $_.valid) {'INVALID'} elseif (@($_.state).Count) {@($_.state) -join ','} else {'stopped'}}} |
            Format-Table -AutoSize
    }
    return
}

if ($args[0] -in @('start', 'up', 'stop', 'down', 'restart', 'status', 'show', 'logs', 'prepare', 'unregister')) {
    $name = Get-TargetName ([string[]]$args)
    if (-not $name) { & wsl.exe -d $distro -- wsl-devctl @args; exit $LASTEXITCODE }
    $wslNames = @((Get-WslProjects) | ForEach-Object { $_.name })
    if ($wslNames -cnotcontains $name) {
        $winNames = @((Get-WinProjects) | ForEach-Object { $_.name })
        if ($winNames -ccontains $name) {
            $forward = @($args)
            if ($forward[0] -eq 'up') { $forward[0] = 'start' }
            if ($forward[0] -eq 'down') { $forward[0] = 'stop' }
            & $win @forward
            return
        }
    }
}

if ($args[0] -in @('start', 'up', 'restart')) { Start-WslKeepAlive }
& wsl.exe -d $distro -- wsl-devctl @args
$resultCode = $LASTEXITCODE
if ($resultCode -eq 0 -and $args[0] -in @('start', 'up', 'restart')) { Start-WslKeepAlive }
exit $resultCode
