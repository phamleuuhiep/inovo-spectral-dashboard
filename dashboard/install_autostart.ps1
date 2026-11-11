# Cài dashboard chạy nền liên tục trong LAN (chạy 1 lần):
#   powershell -ExecutionPolicy Bypass -File dashboard\install_autostart.ps1
# Gỡ:
#   powershell -ExecutionPolicy Bypass -File dashboard\install_autostart.ps1 -Uninstall
#
# - Task Scheduler "InovoDashboard": chạy dashboard\service.pyw khi đăng nhập Windows, tự chạy lại nếu bị dừng
# - Firewall: mở cổng TCP 8050 cho mạng Private (cần quyền admin -> Windows sẽ hỏi UAC một lần)
param([switch]$Uninstall, [switch]$FirewallOnly, [int]$Port = 8050)

$ErrorActionPreference = "Stop"
$TaskName = "InovoDashboard"
$RuleName = "Inovo Dashboard (TCP $Port)"
$Root = Split-Path -Parent $PSScriptRoot
$Service = Join-Path $PSScriptRoot "service.pyw"

function Test-Admin { ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }

function Set-Firewall([bool]$remove) {
    if (-not (Test-Admin)) {
        # chỉ phần firewall cần admin: mở một PowerShell nâng quyền làm riêng việc đó
        $a = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-FirewallOnly", "-Port", $Port)
        if ($remove) { $a += "-Uninstall" }
        try { Start-Process powershell -Verb RunAs -ArgumentList $a -Wait }
        catch { Write-Warning "Không có quyền admin - chưa mở được cổng $Port trên firewall. Máy khác trong LAN có thể bị chặn." }
        return
    }
    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if (-not $remove) {
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private | Out-Null
        Write-Host "Firewall: đã mở TCP $Port (mạng Private)"
    } else { Write-Host "Firewall: đã xoá rule" }
}

if ($FirewallOnly) { Set-Firewall $Uninstall.IsPresent; exit }

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    # dừng service + server đang chạy từ thư mục này
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like "*dashboard*service.pyw*" -or $_.CommandLine -like "*dashboard*server.py*--port $Port*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Set-Firewall $true
    Write-Host "Đã gỡ autostart."
    exit
}

$py = (Get-Command python -ErrorAction Stop).Source
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { throw "Không tìm thấy pythonw.exe cạnh $py" }

$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$Service`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "In-ovo batch dashboard (LAN, cổng $Port) - dashboard\service.pyw" -Force | Out-Null
Write-Host "Task Scheduler: đã tạo '$TaskName' (chạy khi đăng nhập)"

Set-Firewall $false
Start-ScheduledTask -TaskName $TaskName
Start-Sleep 4
$ips = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -in "Dhcp", "Manual" -and $_.IPAddress -notlike "169.254*" -and $_.IPAddress -ne "127.0.0.1" }).IPAddress
Write-Host "Dashboard đang chạy:"
Write-Host "   máy này : http://127.0.0.1:$Port"
$ips | ForEach-Object { Write-Host "   LAN     : http://${_}:$Port" }
Write-Host "Log: $Root\outputs\dashboard_logs\server.log"
