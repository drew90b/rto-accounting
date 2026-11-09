<#
 Read-only discovery for the Eunice Frazer computer. It changes nothing in Frazer or Windows and sends nothing anywhere.
 It writes plain-text reports to C:\FrazerHelper\discovery\ that you can read, then send to Drew.

 Run in PowerShell (right-click Start > Windows PowerShell), from the folder holding this file:
   powershell -ExecutionPolicy Bypass -File .\Check-Frazer.ps1 -Mode Where
   powershell -ExecutionPolicy Bypass -File .\Check-Frazer.ps1 -Mode Watch
   powershell -ExecutionPolicy Bypass -File .\Check-Frazer.ps1 -Mode Controls
   powershell -ExecutionPolicy Bypass -File .\Check-Frazer.ps1 -Mode Session

 -Mode Where     which Frazer is running and from where, any export/schedule/command-line hints, Tailscale, server reachability, RDP sessions
 -Mode Watch     while you run ONE saved export in Frazer, reports every CSV (and other file) that appears, so the real folder is proven
 -Mode Controls  lists the buttons, menus and tabs Windows can see in the Frazer window (no customer names, no cell values)
 -Mode Session   every 10 s logs whether the session is connected/locked and whether Frazer's window is reachable; use it to test RDP disconnect
#>
param([ValidateSet("Where","Watch","Controls","Session")][string]$Mode = "Where",
      [string]$Server = "100.123.116.111", [int]$Port = 8000,
      [string]$ProcessName = "*frazer*", [int]$Minutes = 3)
$ErrorActionPreference = "Continue"
$Out = "C:\FrazerHelper\discovery"; New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Report = Join-Path $Out "$Mode-$Stamp.txt"
function Say($t) { Write-Host $t; Add-Content -Path $Report -Value $t }

function Get-FrazerProcs { Get-Process | Where-Object { $_.ProcessName -like $ProcessName -or $_.MainWindowTitle -like $ProcessName } }

if ($Mode -eq "Where") {
  Say "== Computer =="; Say ("Name: $env:COMPUTERNAME   User: $env:USERNAME   Windows: " + (Get-CimInstance Win32_OperatingSystem).Caption)
  Say "`n== Your session (is this Remote Desktop?) =="; Say ("SESSIONNAME=$env:SESSIONNAME   (console = at the machine; rdp-tcp#N = Remote Desktop)")
  (query user 2>&1) | ForEach-Object { Say $_ }
  Say "`n== Frazer processes (is it local software?) =="
  $p = Get-FrazerProcs
  if (-not $p) { Say "No process matching '$ProcessName'. Start Frazer, sign in, and run this again (or pass -ProcessName)." }
  foreach ($x in $p) {
    $w = Get-CimInstance Win32_Process -Filter "ProcessId=$($x.Id)"
    Say ("{0}  pid {1}  window '{2}'`n   exe: {3}`n   command line: {4}" -f $x.ProcessName, $x.Id, $x.MainWindowTitle, $w.ExecutablePath, $w.CommandLine)
    if ($w.ExecutablePath) {
      $dir = Split-Path $w.ExecutablePath
      Say "   folder: $dir"
      Get-ChildItem $dir -ErrorAction SilentlyContinue -Include *.chm,*.pdf,*.txt,*.ini,*.bat,*.cmd,*.vbs,*.exe -Recurse -Depth 1 |
        Where-Object { $_.Name -match "export|sched|batch|cmd|command|auto|api|odbc|report|readme|help" } |
        Select-Object -First 40 | ForEach-Object { Say ("   hint file: " + $_.FullName) }
    }
  }
  Say "`n== Installed programs matching Frazer =="
  Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*","HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -like "*frazer*" } | ForEach-Object { Say ("{0} {1}  at {2}" -f $_.DisplayName, $_.DisplayVersion, $_.InstallLocation) }
  Say "`n== Scheduled tasks that mention Frazer =="
  Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { ($_.TaskName + ($_.Actions | Out-String)) -match "frazer" } | ForEach-Object { Say ("{0}  {1}" -f $_.TaskName, ($_.Actions | ForEach-Object { $_.Execute + " " + $_.Arguments })) }
  Say "`n== Drives (a \\tsclient drive here is YOUR LAPTOP, not this computer) =="
  Get-PSDrive -PSProvider FileSystem | ForEach-Object { Say ("{0}: {1}  free {2:N1} GB" -f $_.Name, $_.DisplayRoot, ($_.Free / 1GB)) }
  Say "`n== Candidate export folders (newest CSV files, local drives only) =="
  foreach ($d in @("C:\Users\$env:USERNAME\Documents","C:\Users\$env:USERNAME\Downloads","C:\Users\$env:USERNAME\Desktop","C:\Frazer","C:\Export","C:\Exports","C:\")) {
    if (Test-Path $d) { Get-ChildItem $d -Filter *.csv -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 5 | ForEach-Object { Say ("{0}  {1:yyyy-MM-dd HH:mm}  {2:N0} bytes" -f $_.FullName, $_.LastWriteTime, $_.Length) } }
  }
  Say "`n== Tailscale =="
  $ts = Get-Command tailscale -ErrorAction SilentlyContinue; if (-not $ts -and (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) { $ts = "C:\Program Files\Tailscale\tailscale.exe" }
  if ($ts) { (& $ts status 2>&1 | Select-Object -First 12) | ForEach-Object { Say $_ } } else { Say "tailscale.exe not found: Tailscale is not installed on this computer." }
  Say "`n== Can this computer reach the RTO server? =="
  $t = Test-NetConnection -ComputerName $Server -Port $Port -WarningAction SilentlyContinue
  Say ("{0}:{1}  reachable = {2}" -f $Server, $Port, $t.TcpTestSucceeded)
  Say "`n== Python (needed by the helper) =="; (python --version 2>&1) | ForEach-Object { Say $_ }
  Say "`n== Screen lock / sleep settings =="
  (powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE 2>&1 | Select-String "Current AC|Current DC") | ForEach-Object { Say $_.Line }
  Say ("Screen saver secure: " + (Get-ItemProperty "HKCU:\Control Panel\Desktop" -Name ScreenSaverIsSecure -ErrorAction SilentlyContinue).ScreenSaverIsSecure)
}

if ($Mode -eq "Watch") {
  Say "Watching every local drive for new or changed files for $Minutes minutes. NOW run ONE saved export in Frazer (note which preset), then wait for this to finish."
  $skip = '\\Windows\\|\\ProgramData\\|\\AppData\\Local\\(Temp|Microsoft|Packages)|\\\$Recycle|\\System Volume|\\FrazerHelper\\discovery|\\Tailscale\\|\\Google\\|\\Microsoft\\'
  $global:FzEvents = New-Object System.Collections.ArrayList
  $subs = @()
  foreach ($d in (Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Root -match '^[A-Z]:\\$' })) {
    $w = New-Object System.IO.FileSystemWatcher $d.Root; $w.IncludeSubdirectories = $true; $w.NotifyFilter = "FileName,LastWrite,Size"
    foreach ($ev in "Created","Changed","Renamed") {
      $subs += Register-ObjectEvent $w $ev -MessageData $skip -Action { if ($Event.SourceEventArgs.FullPath -notmatch $Event.MessageData -and -not (Test-Path $Event.SourceEventArgs.FullPath -PathType Container)) { $null = $global:FzEvents.Add(@((Get-Date), $Event.SourceEventArgs.ChangeType, $Event.SourceEventArgs.FullPath)) } }
    }
    $w.EnableRaisingEvents = $true
  }
  Start-Sleep -Seconds ($Minutes * 60)
  Say "`n== Files that appeared or changed (unique) =="
  $global:FzEvents | Group-Object { $_[2] } | ForEach-Object {
    $path = $_.Name; $len = (Get-Item $path -ErrorAction SilentlyContinue).Length
    Say ("{0}  {1:HH:mm:ss}  {2} bytes  {3}" -f $path, $_.Group[-1][0], $len, $(if ($path -match "\.csv$") {"<-- CSV"} else {""}))
  }
  Say "`nSend this report to Drew. The folder of the CSV above is the real export location."
  $subs | ForEach-Object { Unregister-Event -SubscriptionId $_.Id -ErrorAction SilentlyContinue }
}

if ($Mode -eq "Controls") {
  Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
  $p = Get-FrazerProcs | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
  if (-not $p) { Say "No Frazer window found. Start Frazer, open the EXPORT screen (not a customer), and run again."; return }
  Say ("Window: '{0}'  (pid {1}). Open the export / saved-presets screen first; customer records should NOT be on screen." -f $p.MainWindowTitle, $p.Id)
  $root = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
  $keep = "Button","MenuItem","Menu","MenuBar","TabItem","Hyperlink","CheckBox","RadioButton","ComboBox","ToolBar","Window","Pane","Group","Tree","TreeItem"
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  Say "Elements Windows can see in total: $($all.Count)  (a very small number means Frazer draws its own controls and cannot be driven reliably by control name)"
  $n = 0
  foreach ($e in $all) {
    $t = $e.Current.ControlType.ProgrammaticName -replace "ControlType\.",""
    if ($keep -contains $t -and $n -lt 400) { $n++; Say ("{0,-12} name='{1}' id='{2}' class='{3}' enabled={4}" -f $t, $e.Current.Name, $e.Current.AutomationId, $e.Current.ClassName, $e.Current.IsEnabled) }
  }
  Say "(names of text boxes, grid cells and list rows are deliberately not recorded)"
}

if ($Mode -eq "Session") {
  Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
  Say "Logging every 10 s for $Minutes minutes. Start it, then DISCONNECT Remote Desktop (close the window with the X; do NOT sign out), wait 2 minutes, reconnect, and send this report."
  $end = (Get-Date).AddMinutes($Minutes)
  while ((Get-Date) -lt $end) {
    $locked = [bool](Get-Process LogonUI -ErrorAction SilentlyContinue)
    $p = Get-FrazerProcs | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    $found = "no window"; if ($p) { try { $e = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle); $found = "reachable, " + $e.Current.BoundingRectangle } catch { $found = "ERROR " + $_.Exception.Message } }
    $sess = (query session $env:USERNAME 2>&1 | Select-Object -Skip 1 | ForEach-Object { ($_ -replace '\s+',' ').Trim() }) -join " | "
    Say ("{0:HH:mm:ss}  locked={1}  frazer={2}  session: {3}" -f (Get-Date), $locked, $found, $sess)
    Start-Sleep -Seconds 10
  }
}
Write-Host "`nReport saved: $Report"
