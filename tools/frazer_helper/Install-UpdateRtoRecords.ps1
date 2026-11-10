<#
 Install-UpdateRtoRecords.ps1: one-time setup of "Update RTO Records" on the Frazer computer. Windows PowerShell 5.1.
 No administrator rights. Everything goes under C:\FrazerHelper; nothing in Frazer's own folders or Windows settings is changed.

 Set these variables first (the one-line install command does it), then run this script's text:
   $FzSource  where to fetch the helper's files (http://server:port)
   $FzServer  the RTO app the helper uploads to
   $FzToken   the helper token created on the app's Helper page (starts fxh_)
   $FzFirst   the explicit start date of the FIRST transaction period (YYYY-MM-DD)
   $FzPresets $true to save pictures of the three saved exports and rename them to the standard names (default $true);
              $false leaves Frazer's saved exports alone and only reads their names
   $FzWant    optional: the names wanted for File 1, 2, 3, e.g. @{ '1' = 'FRZDATA1'; '2' = 'frzr-transctions' } to put the
              earlier names back (recovery). Default: frazer-vehicles, frazer-payment-transactions, frazer-customer-balances

 Recovery: every step can be run again. If it stops part-way, run the same command again; Python is not reinstalled, the saved
 exports are photographed again before anything is renamed, and the settings file is rewritten from the names Frazer shows.
 A rename is saved only after the new name reads back exactly, and afterwards every settings view is compared with its
 picture from before. Pictures and names from each run stay in C:\FrazerHelper\preset-backup\<date_time>\.

 Steps: folders; helper files; Python for this user only (python.org installer, signature checked); settings; desktop shortcut;
 the saved-export names in Frazer (pictures first, then rename, then read back); a connection check. Safe to run again.
#>
$ErrorActionPreference = 'Stop'
$Root = 'C:\FrazerHelper'; $App = "$Root\app"; $Py = "$Root\python"
$Stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$LogDir = "$Root\setup\$Stamp"
foreach ($d in $Root, $App, "$Root\runs", $LogDir) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
$Log = "$LogDir\setup.txt"
function Say($t) { Write-Host $t; Add-Content -Path $Log -Value $t }
function Step($t) { Say ''; Say ("== " + $t + " ==") }
if (-not (Get-Variable FzPresets -ErrorAction SilentlyContinue)) { $FzPresets = $true }
$problems = @()
try {
  Say ("Update RTO Records setup, {0:yyyy-MM-dd HH:mm:ss} local, computer {1}, user {2}, PowerShell {3}" -f (Get-Date), $env:COMPUTERNAME, $env:USERNAME, $PSVersionTable.PSVersion)
  foreach ($v in 'FzSource', 'FzServer', 'FzToken', 'FzFirst') { if (-not (Get-Variable $v -ValueOnly -ErrorAction SilentlyContinue)) { throw "The setting `$$v is missing." } }
  if (-not (Test-Path 'C:\FrazerData')) { throw 'C:\FrazerData does not exist on this computer.' }

  Step '1. Helper files'
  foreach ($f in 'frazer_helper.py', 'FrazerDriver.ps1') {
    Invoke-WebRequest -Uri "$FzSource/f/$f" -OutFile "$App\$f" -UseBasicParsing
    Say ("{0}  {1} bytes  sha256 {2}" -f $f, (Get-Item "$App\$f").Length, (Get-FileHash "$App\$f" -Algorithm SHA256).Hash.ToLower())
  }

  Step '2. Python (this user only, in C:\FrazerHelper\python)'
  if (-not (Test-Path "$Py\pythonw.exe")) {
    $inst = "$env:TEMP\python-3.12.10-amd64.exe"
    Say 'Downloading the Python 3.12.10 installer from python.org (about 26 MB)...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $inst -UseBasicParsing
    $sig = Get-AuthenticodeSignature $inst
    Say ("installer signature: {0}; signer: {1}" -f $sig.Status, $sig.SignerCertificate.Subject)
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'The Python installer is not signed by the Python Software Foundation. It was not run.' }
    Say 'Installing (quiet, no administrator rights; about a minute)...'
    $p = Start-Process -FilePath $inst -Wait -PassThru -ArgumentList '/quiet', 'InstallAllUsers=0', "TargetDir=$Py", 'Include_launcher=0', 'InstallLauncherAllUsers=0', 'Include_test=0', 'Include_doc=0', 'Include_dev=0', 'Shortcuts=0', 'AssociateFiles=0', 'PrependPath=0', 'Include_tcltk=1', 'Include_pip=0'
    Say ("installer exit code: {0}" -f $p.ExitCode)
    Remove-Item $inst -Force -ErrorAction SilentlyContinue
  } else { Say 'Already installed.' }
  if (-not (Test-Path "$Py\pythonw.exe")) { throw 'Python was not installed (pythonw.exe is missing).' }
  $ver = (& "$Py\python.exe" -c "import sys, tkinter, ssl, json; print(sys.version.split()[0], 'tkinter', tkinter.TkVersion)" 2>&1) -join ' '
  Say ("python: " + $ver)
  if ($ver -notmatch 'tkinter') { throw ('Python does not run correctly: ' + $ver) }

  Step '3. Saved export names in Frazer'
  $names = @{ '1' = 'FRZDATA1'; '2' = 'frzr-transctions'; '3' = 'frazer-customer-balances' }      # as last seen; replaced below by what Frazer shows
  $want = @{ '1' = 'frazer-vehicles'; '2' = 'frazer-payment-transactions'; '3' = 'frazer-customer-balances' }
  $fallback = @{ '2' = 'frazer-payments' }
  if (Get-Variable FzWant -ErrorAction SilentlyContinue) { foreach ($k in @($FzWant.Keys)) { $want["$k"] = [string]$FzWant[$k]; $fallback.Remove("$k") } }
  $slots = @(
    @{ slot = 1; type = 'vehicles'; want = $want['1'] },
    @{ slot = 3; type = 'customer_balances'; want = $want['3'] },
    @{ slot = 2; type = 'payments'; want = $want['2']; fallback = $fallback['2'] })
  if (-not $FzPresets) {
    $bk = "$Root\preset-backup\$Stamp"; New-Item -ItemType Directory -Force -Path $bk | Out-Null
    $resPath = "$bk\names-result.json"
    Say 'Reading the saved export names only (nothing in Frazer is changed). Hands off for about 20 seconds...'
    Start-Sleep -Seconds 3
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$App\FrazerDriver.ps1" -Action Names -ResultPath $resPath -OutDir $bk | Out-Null
    if (Test-Path $resPath) {
      $r = Get-Content -Raw $resPath | ConvertFrom-Json
      if ($r.names) { foreach ($k in '1', '2', '3') { if ($r.names.$k) { $names[$k] = [string]$r.names.$k } }; Say ("names in Frazer: File 1 '{0}', File 2 '{1}', File 3 '{2}'" -f $names['1'], $names['2'], $names['3']) }
      if ($r.stopped) { $problems += ('Reading the names: ' + $r.stopped); Say ('STOPPED: ' + $r.stopped) }
    } else { $problems += 'The Frazer driver gave no result when reading the names.' }
  }
  if ($FzPresets) {
    $bk = "$Root\preset-backup\$Stamp"; New-Item -ItemType Directory -Force -Path $bk | Out-Null
    # the column headings of the files Frazer has written so far are part of each saved export's configuration (headings only)
    Get-ChildItem C:\FrazerData -Filter *.csv -File | ForEach-Object { Add-Content -Path "$bk\current-file-headings.txt" -Value ("{0} | written {1:yyyy-MM-dd HH:mm:ss} | {2} bytes`r`n{3}`r`n" -f $_.Name, $_.LastWriteTime, $_.Length, (Get-Content $_.FullName -TotalCount 1)) }
    $reqPath = "$bk\presets-request.json"; $resPath = "$bk\presets-result.json"
    (@{ out_dir = $bk; slots = $slots } | ConvertTo-Json -Depth 5) | Out-File -FilePath $reqPath -Encoding ascii
    Say 'Saving pictures of the three saved exports, then renaming them. Hands off the mouse and keyboard for about two minutes...'
    Start-Sleep -Seconds 3
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$App\FrazerDriver.ps1" -Action Presets -RequestPath $reqPath -ResultPath $resPath | Out-Null
    if (Test-Path $resPath) {
      $r = Get-Content -Raw $resPath | ConvertFrom-Json
      if ($r.names_before) { Say ("names before: File 1 '{0}', File 2 '{1}', File 3 '{2}', File 4 '{3}'" -f $r.names_before.'1', $r.names_before.'2', $r.names_before.'3', $r.names_before.'4') }
      Say ("pictures of the saved exports kept in {0}: {1}" -f $bk, [bool]$r.preserved)
      foreach ($x in @($r.renames)) { if ($x) { Say ("File {0}: '{1}' -> wanted '{2}': {3}" -f $x.slot, $x.from, $x.want, $x.result) } }
      if ($r.names_after) { Say ("names now (read back from Frazer): File 1 '{0}', File 2 '{1}', File 3 '{2}'" -f $r.names_after.'1', $r.names_after.'2', $r.names_after.'3') }
      if ($r.names_after) {
        if ($r.settings_unchanged) { Say ("settings check: all {0} settings views look exactly as they did before the rename" -f $r.settings_views_compared) }
        else { $problems += ('Saved export settings look different after the rename: ' + (@($r.settings_changed) -join '; ') + '. Compare the before- and after- pictures in ' + $bk); Say ('SETTINGS DIFFER after the rename: ' + (@($r.settings_changed) -join '; ')) }
      }
      $src = $null; if ($r.names_after) { $src = $r.names_after } elseif ($r.names_before) { $src = $r.names_before }
      if ($src) { foreach ($k in '1', '2', '3') { if ($src.$k) { $names[$k] = [string]$src.$k } } }
      if ($r.stopped) { $problems += ('Saved exports: ' + $r.stopped); Say ('STOPPED: ' + $r.stopped) }
      foreach ($e in @($r.diagnostics.errors)) { if ($e) { Say ('diagnostic note: ' + $e) } }
    } else { $problems += 'The Frazer driver gave no result for the saved exports.'; Say 'The Frazer driver gave no result.' }
  }

  Step '4. Settings'
  $cfg = [ordered]@{
    server = $FzServer; token = $FzToken; landing_folder = 'C:\FrazerData'; work_folder = "$Root\runs"
    mode = 'auto'; driver = "$App\FrazerDriver.ps1"; first_coverage_start = $FzFirst
    exports = [ordered]@{
      vehicles          = [ordered]@{ label = 'File 1 (vehicles)'; slot = 1; preset = $names['1']; file = ($names['1'] + '.CSV') }
      customer_balances = [ordered]@{ label = 'File 3 (customer balances)'; slot = 3; preset = $names['3']; file = ($names['3'] + '.CSV') }
      payments          = [ordered]@{ label = 'File 2 (payment transactions)'; slot = 2; preset = $names['2']; file = ($names['2'] + '.CSV') }
    }
  }
  [System.IO.File]::WriteAllText("$Root\config.json", ($cfg | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding $false))
  Say ("C:\FrazerHelper\config.json written: server {0}; first transaction period starts {1}; working files {2}, {3}, {4}" -f $FzServer, $FzFirst, $cfg.exports.vehicles.file, $cfg.exports.customer_balances.file, $cfg.exports.payments.file)

  Step '5. Desktop shortcut'
  $desk = [Environment]::GetFolderPath('Desktop')
  $ws = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut("$desk\Update RTO Records.lnk")
  $lnk.TargetPath = "$Py\pythonw.exe"
  $lnk.Arguments = "`"$App\frazer_helper.py`" window --go --config `"$Root\config.json`""
  $lnk.WorkingDirectory = $App
  $lnk.Description = 'Create the three Frazer exports and send them to the RTO app'
  $lnk.Save()
  Say ("Shortcut: $desk\Update RTO Records.lnk")

  Step '6. Connection check'
  $chk = (& "$Py\python.exe" "$App\frazer_helper.py" check --config "$Root\config.json" 2>&1) -join "`r`n"
  Say $chk
  if ($chk -match 'STOPPED|NOT FOUND') { $problems += 'The connection check reported a problem (see above).' }
} catch {
  $problems += $_.Exception.Message
  Say ('STOPPED: ' + $_.Exception.Message)
}
Say ''
if ($problems.Count) { Say 'SETUP FINISHED WITH PROBLEMS:'; foreach ($p in $problems) { Say ('  - ' + $p) } } else { Say 'SETUP COMPLETE. Next: sign in to Hosted Frazer, leave it on its main screen, and double-click "Update RTO Records" on the desktop.' }
# send the setup log and the pictures of the saved exports back to where the files came from (no customer data: settings screens only)
try {
  $sent = 0
  $files = @(Get-ChildItem $LogDir -File) + @(Get-ChildItem "$Root\preset-backup\$Stamp" -File -ErrorAction SilentlyContinue)
  foreach ($f in $files) { try { Invoke-WebRequest -Uri ("$FzSource/u/setup-" + $f.Name) -Method Put -InFile $f.FullName -UseBasicParsing | Out-Null; $sent++ } catch {} }
  Write-Host ("Sent $sent setup files to Drew's server. Log: $Log")
} catch {}
