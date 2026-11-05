# Registers a Windows scheduled task that starts RDS Bridge when the PC boots
# and restarts it if it ever stops. Run via "Install autostart.bat".
$ErrorActionPreference = "Stop"
$TaskName = "RDS Bridge"
$dir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$config = Join-Path $dir "config.json"
$exe = Join-Path $dir "RDSBridge.exe"

try {
    if (Test-Path $exe) {
        $program = $exe
        $arguments = "--config `"$config`""
    } else {
        $program = $null
        if (Get-Command py -ErrorAction SilentlyContinue) { $program = (& py -3 -c "import sys; print(sys.executable)").Trim() }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { $program = (& python -c "import sys; print(sys.executable)").Trim() }
        if (-not $program) { throw "Neither RDSBridge.exe nor Python 3 was found." }
        $arguments = "-m rdsbridge --config `"$config`""
    }

    Write-Host ""
    Write-Host "RDS Bridge will start automatically when Windows starts."
    Write-Host ""
    Write-Host "Which Windows account should it run as?"
    Write-Host "  - Press Enter for SYSTEM (fine when WideOrbit's XML file is on this PC or sent over the network)."
    Write-Host "  - Type an account such as DOMAIN\user if the XML file is on a network share,"
    Write-Host "    because SYSTEM cannot read most network shares."
    $account = Read-Host "Account"

    $action = New-ScheduledTaskAction -Execute $program -Argument $arguments -WorkingDirectory $dir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew

    if ([string]::IsNullOrWhiteSpace($account) -or $account -eq "SYSTEM") {
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Force | Out-Null
    } else {
        $cred = Get-Credential -UserName $account -Message "Password for $account"
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Highest -Force | Out-Null
    }

    # Let WideOrbit reach the UDP/TCP/HTTP inputs through Windows Firewall.
    Get-NetFirewallRule -DisplayName $TaskName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $TaskName -Direction Inbound -Program $program -Action Allow | Out-Null

    Start-ScheduledTask -TaskName $TaskName
    Write-Host ""
    Write-Host "Done. RDS Bridge is running in the background and will start with Windows." -ForegroundColor Green
    Write-Host "Open http://localhost:8080/ (or your configured web port) to see it."
    Write-Host "Do not also run 'Start RDS Bridge.bat' while the background copy is running."
} catch {
    Write-Host ""
    Write-Host "Failed: $($_.Exception.Message)" -ForegroundColor Red
}
Write-Host ""
Read-Host "Press Enter to close"
