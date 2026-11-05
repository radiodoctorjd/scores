# Removes the RDS Bridge scheduled task and firewall rule. Run via "Remove autostart.bat".
$TaskName = "RDS Bridge"
try {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Autostart removed and the background copy stopped." -ForegroundColor Green
    } else {
        Write-Host "Autostart was not installed."
    }
    Get-NetFirewallRule -DisplayName $TaskName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
} catch {
    Write-Host "Failed: $($_.Exception.Message)" -ForegroundColor Red
}
Write-Host ""
Read-Host "Press Enter to close"
