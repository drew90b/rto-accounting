<#
 FrazerDriver.ps1: makes Hosted Frazer create the saved exports. Windows PowerShell 5.1, nothing to install.
 Started by frazer_helper.py ("Update RTO Records"); it can also be run by hand for setup.

 Hosted Frazer shows each Frazer window as its own Windows window (title "... (fz-NNNNNN.hostedfrazer.com)"), but the controls
 inside are a picture. So this script identifies windows by title, sends a few keys, clicks only buttons whose place it has
 checked, reads a text field back by copying it, and reads which options are selected from the dots on screen.

 It never types a password, never opens a customer, never presses Select All / Unselect All or ticks a field, and never clicks
 Save File Specifications except in -Action Presets (renaming a saved export, on request). It stops rather than guess: if the
 window in front is not the one expected, no more input is sent. Success is never decided here: the helper checks the files.

   -Action Export   -RequestPath req.json -ResultPath res.json     create the exports listed in the request
   -Action Names    -ResultPath res.json [-OutDir dir]             read the four saved export names (changes nothing)
   -Action Presets  -RequestPath req.json -ResultPath res.json     save pictures of the saved exports, then rename as requested

 Every screenshot is a diagnostic: a failure to save one is recorded under "diagnostics" and never changes an export's result.
 Positions below are for the Export Data window at its fixed size of 1016 x 656 pixels; any other size stops the script.
#>
param([ValidateSet("Export", "Names", "Presets")][string]$Action = "Names",
      [string]$RequestPath = "", [string]$ResultPath = "", [string]$OutDir = "")
$ErrorActionPreference = "Stop"
$DriverVersion = "1.0"

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class FzDrv {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Explicit)] public struct U { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public U u; }
  [DllImport("user32.dll", SetLastError=true)] static extern uint SendInput(uint n, INPUT[] p, int cb);
  [DllImport("user32.dll")] static extern uint MapVirtualKey(uint code, uint mapType);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern short GetKeyState(int vk);
  [DllImport("user32.dll")] static extern bool AttachThreadInput(uint a, uint b, bool attach);
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int n);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  // one line per visible top-level window: handle|pid|class|width|height|title
  public static System.Collections.Generic.List<string> Windows() {
    var list = new System.Collections.Generic.List<string>();
    EnumWindows(delegate(IntPtr h, IntPtr l) {
      if (!IsWindowVisible(h)) return true;
      RECT r; GetWindowRect(h, out r);
      var t = new System.Text.StringBuilder(256); GetWindowText(h, t, 256);
      var c = new System.Text.StringBuilder(256); GetClassName(h, c, 256);
      uint pid; GetWindowThreadProcessId(h, out pid);
      list.Add(h.ToInt64() + "|" + pid + "|" + c + "|" + (r.R - r.L) + "|" + (r.B - r.T) + "|" + t);
      return true;
    }, IntPtr.Zero);
    return list;
  }
  public static void Click(int x, int y) {
    SetCursorPos(x, y); System.Threading.Thread.Sleep(250);
    INPUT[] a = new INPUT[1]; a[0].type = 0; a[0].u.mi.dwFlags = 2; SendInput(1, a, Marshal.SizeOf(typeof(INPUT)));
    System.Threading.Thread.Sleep(80);
    a[0].u.mi.dwFlags = 4; SendInput(1, a, Marshal.SizeOf(typeof(INPUT)));
  }
  public static uint Key(ushort vk, bool up) {
    INPUT[] a = new INPUT[1];
    a[0].type = 1; a[0].u.ki.wVk = vk; a[0].u.ki.wScan = (ushort)MapVirtualKey(vk, 0);
    a[0].u.ki.dwFlags = up ? 2u : 0u;
    return SendInput(1, a, Marshal.SizeOf(typeof(INPUT)));
  }
  // Ask Windows to put a window in front; if it refuses, borrow the input queue of the window that is in front and ask again.
  public static bool ForceForeground(IntPtr h) {
    if (GetForegroundWindow() == h) return true;
    SetForegroundWindow(h);
    if (GetForegroundWindow() == h) return true;
    uint pid; uint fg = GetWindowThreadProcessId(GetForegroundWindow(), out pid); uint me = GetCurrentThreadId();
    if (fg != 0 && fg != me) { AttachThreadInput(me, fg, true); BringWindowToTop(h); SetForegroundWindow(h); AttachThreadInput(me, fg, false); }
    return GetForegroundWindow() == h;
  }
}
"@
[void][FzDrv]::SetProcessDPIAware()

# ---- result and diagnostics ------------------------------------------------------------------------------------------------
$script:Diag = @{ screenshots = New-Object System.Collections.ArrayList; errors = New-Object System.Collections.ArrayList; log = New-Object System.Collections.ArrayList }
$script:ShotN = 0
$script:M8 = [IntPtr]::Zero
function Note($t) { [void]$script:Diag.log.Add(("{0:HH:mm:ss} {1}" -f (Get-Date), $t)) }
function DiagError($t) { [void]$script:Diag.errors.Add($t); Note ("DIAGNOSTIC PROBLEM: " + $t) }

# A screenshot is evidence for a person, never a condition of success. It cannot throw.
function Shot($hwnd, $name) {
  try {
    if (-not $OutDir) { return }
    $r = New-Object FzDrv+RECT
    if (-not [FzDrv]::GetWindowRect($hwnd, [ref]$r)) { DiagError "screenshot ${name}: the window no longer exists"; return }
    $w = $r.R - $r.L; $ht = $r.B - $r.T
    if ($w -le 0 -or $ht -le 0) { DiagError "screenshot ${name}: the window has no size (already closed)"; return }
    $bmp = New-Object System.Drawing.Bitmap $w, $ht
    $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size); $g.Dispose()
    $f = Join-Path $OutDir ('{0:d2}-{1}.png' -f $script:ShotN, $name); $script:ShotN++
    $bmp.Save($f, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
    [void]$script:Diag.screenshots.Add((Split-Path $f -Leaf))
  } catch { DiagError ("screenshot ${name}: " + $_.Exception.Message) }
}

# SHA-256 of the pixels of part of a window: used to show a saved export's settings look exactly the same before and after.
function RegionHash($hwnd, $x, $y, $w, $h) {
  $r = New-Object FzDrv+RECT; [void][FzDrv]::GetWindowRect($hwnd, [ref]$r)
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($r.L + $x, $r.T + $y, 0, 0, $bmp.Size); $g.Dispose()
  $ms = New-Object System.IO.MemoryStream
  $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Bmp); $bmp.Dispose()
  $sha = [Security.Cryptography.SHA256]::Create()
  $hx = ([BitConverter]::ToString($sha.ComputeHash($ms.ToArray())) -replace '-', '').ToLower()
  $ms.Dispose()
  return $hx
}

function WriteResult($obj) {
  $obj["driver_version"] = $DriverVersion
  $obj["diagnostics"] = @{ screenshots = @($script:Diag.screenshots); errors = @($script:Diag.errors); log = @($script:Diag.log) }
  $json = $obj | ConvertTo-Json -Depth 8
  if ($ResultPath) { [System.IO.File]::WriteAllText($ResultPath, $json, (New-Object System.Text.UTF8Encoding $false)) } else { Write-Output $json }
}

# ---- keys, windows ---------------------------------------------------------------------------------------------------------
function Tap([int]$vk) { [void][FzDrv]::Key($vk, $false); Start-Sleep -Milliseconds 60; [void][FzDrv]::Key($vk, $true); Start-Sleep -Milliseconds 60 }
function AltTap([int]$vk) { [void][FzDrv]::Key(0x12, $false); Start-Sleep -Milliseconds 80; Tap $vk; [void][FzDrv]::Key(0x12, $true); Start-Sleep -Milliseconds 80 }
function ShiftTab { [void][FzDrv]::Key(0x10, $false); Start-Sleep -Milliseconds 80; Tap 0x09; [void][FzDrv]::Key(0x10, $true); Start-Sleep -Milliseconds 300 }
# digits, lower-case letters, "/" and "-" only: all this script ever types
function TypeText($t) {
  foreach ($ch in $t.ToCharArray()) {
    if ($ch -eq '/') { Tap 0xBF } elseif ($ch -eq '-') { Tap 0xBD }
    elseif ($ch -match '[0-9]') { Tap ([int][char]$ch) }
    elseif ($ch -cmatch '[a-z]') { Tap ([int][char]([string]$ch).ToUpper()) }
    else { throw "This script does not type the character '$ch'." }
  }
  Start-Sleep -Milliseconds 300
}
# Ctrl+C in the focused Frazer field, then read this computer's clipboard
function CopyField {
  try { [System.Windows.Forms.Clipboard]::Clear() } catch {}
  [void][FzDrv]::Key(0x11, $false); Start-Sleep -Milliseconds 80; Tap 0x43; [void][FzDrv]::Key(0x11, $true)
  Start-Sleep -Milliseconds 900
  $t = ''
  try { $t = [System.Windows.Forms.Clipboard]::GetText() } catch { $t = '' }
  return $t
}
function Wins {
  $o = @()
  foreach ($line in [FzDrv]::Windows()) {
    $a = $line.Split('|', 6)
    if ($a[2] -match 'RAIL' -and [int]$a[3] -gt 0) { $o += [pscustomobject]@{ H = [IntPtr][int64]$a[0]; W = [int]$a[3]; Ht = [int]$a[4]; Title = $a[5] } }
  }
  return $o
}
function M8Wins { return @(Wins | Where-Object { $_.Title -like 'M-8*Export Data*' }) }
function MainWin { return (Wins | Where-Object { $_.Title -like 'Frazer (*' -and $_.W -ge 800 } | Select-Object -First 1) }
function Exists($hwnd) { return [bool](Wins | Where-Object { $_.H -eq $hwnd }) }
function Front { if ([FzDrv]::GetForegroundWindow() -ne $script:M8) { throw 'The Export Data window is not in front. Stopped; no more input was sent.' } }
function WinRect($hwnd) { $r = New-Object FzDrv+RECT; [void][FzDrv]::GetWindowRect($hwnd, [ref]$r); return $r }
function M8Rect {
  $r = WinRect $script:M8
  if (($r.R - $r.L) -ne 1016 -or ($r.B - $r.T) -ne 656) { throw ("The Export Data window is {0}x{1}, not the 1016x656 this script was checked against. Nothing was clicked." -f ($r.R - $r.L), ($r.B - $r.T)) }
  return $r
}

# Ask an Export Data window to close (the same as its X button). Saves nothing.
function CloseM8($hwnd) {
  if (-not (Exists $hwnd)) { return $true }
  [void][FzDrv]::PostMessage($hwnd, 0x0112, [IntPtr]0xF060, [IntPtr]::Zero)
  for ($i = 0; $i -lt 20; $i++) { Start-Sleep -Milliseconds 400; if (-not (Exists $hwnd)) { return $true } }
  Note 'An Export Data window did not close on request.'
  return $false
}

# From the main Frazer window: Alt+M, then 8, and wait for a NEW Export Data window to come to the front
function OpenM8 {
  $mainw = MainWin
  if (-not $mainw) { throw 'Frazer is not open. Open Hosted Frazer, sign in, and leave it on its main screen.' }
  $extra = @(Wins | Where-Object { $_.Title -notlike 'Frazer (*' -and $_.Title -notlike 'M-8*Export Data*' })
  if ($extra.Count) { throw ("Another Frazer screen is open ('{0}'). Close it and leave Frazer on its main screen." -f ($extra[0].Title -replace ' \(fz-.*$', '')) }
  $old = @(M8Wins | ForEach-Object { $_.H })
  if ([FzDrv]::IsIconic($mainw.H)) { [void][FzDrv]::ShowWindow($mainw.H, 9); Start-Sleep -Milliseconds 600 }
  for ($i = 0; $i -lt 10; $i++) { if ([FzDrv]::ForceForeground($mainw.H)) { break }; Start-Sleep -Milliseconds 400 }
  if ([FzDrv]::GetForegroundWindow() -ne $mainw.H) { throw 'The Frazer window could not be brought to the front (is the screen locked, or another program in the way?). No input was sent.' }
  AltTap 0x4D; Start-Sleep -Milliseconds 700; Tap 0x38
  $new = $null
  for ($i = 0; $i -lt 40; $i++) { Start-Sleep -Milliseconds 400; $new = M8Wins | Where-Object { $old -notcontains $_.H } | Select-Object -First 1; if ($new) { break } }
  if (-not $new) { Shot $mainw.H 'export-data-did-not-open'; Tap 0x1B; Start-Sleep -Milliseconds 300; Tap 0x1B; throw 'Export Data did not open after Alt+M, 8 (Frazer may not be on its main screen). Pressed Esc twice and stopped.' }
  $script:M8 = $new.H
  Start-Sleep -Milliseconds 1000
  for ($i = 0; $i -lt 10; $i++) { if ([FzDrv]::ForceForeground($new.H)) { break }; Start-Sleep -Milliseconds 400 }
  Front
  [void](M8Rect)
}

# ---- reading the screen ----------------------------------------------------------------------------------------------------
# A selected option shows a dot (black, or blue when the pointer is over it); an unselected one is white inside.
function Dot($bmp, $cx, $cy) { $n = 0; for ($x = $cx - 2; $x -le $cx + 2; $x++) { for ($y = $cy - 2; $y -le $cy + 2; $y++) { $c = $bmp.GetPixel($x, $y); if (($c.R + $c.G + $c.B) -lt 560) { $n++ } } }; return $n }
$SlotY = @{ 1 = 76; 2 = 104; 3 = 130; 4 = 156 }
$Sale = @('Type of Sale: All', 523, 79); $Csv = @('File Type: CSV', 17, 225)
$TypeChecks = @{
  'vehicles'          = @(@('C - All Vehicles', 225, 117), $Sale, @('Vehicle Status: B - Both', 899, 172), $Csv)
  'customer_balances' = @(@('A - All Customers with Balances', 225, 74), $Sale, $Csv)
  'payments'          = @(@('F - All Payment Transactions by Date', 225, 178), $Sale, $Csv)
}
# Immediately before Create File: the expected slot, data type and options must be the ones selected on screen
function ConfirmPreset($type, $slot) {
  Front
  $r = M8Rect
  [void][FzDrv]::SetCursorPos($r.L + 600, $r.T + 12); Start-Sleep -Milliseconds 500      # park the pointer on the title bar
  $bmp = New-Object System.Drawing.Bitmap 1016, 656
  $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size); $g.Dispose()
  $checks = @(, @("File $slot slot", 17, $SlotY[[int]$slot])) + $TypeChecks[$type]
  $list = @(); $bad = @()
  foreach ($c in $checks) { $ok = ((Dot $bmp $c[1] $c[2]) -ge 20); $list += @{ what = $c[0]; selected = $ok }; if (-not $ok) { $bad += $c[0] } }
  $bmp.Dispose()
  return @{ list = $list; bad = $bad }
}
# Click a button only where its label colour is found (green "Create File"; any dark text for "Save File Specifications")
function ClickButton($name, $x0, $y0, $w, $h, $cx, $cy, $green) {
  Front
  $r = M8Rect
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($r.L + $x0, $r.T + $y0, 0, 0, $bmp.Size); $g.Dispose()
  $n = 0
  for ($x = 0; $x -lt $w; $x++) { for ($y = 0; $y -lt $h; $y++) { $c = $bmp.GetPixel($x, $y)
    if ($green) { if ($c.G -gt 90 -and $c.R -lt 90 -and $c.B -lt 90) { $n++ } } else { if (($c.R + $c.G + $c.B) -lt 200) { $n++ } } } }
  $bmp.Dispose()
  if ($n -lt 40) { throw "The $name button was not found where expected. Nothing was clicked." }
  [FzDrv]::Click($r.L + $cx, $r.T + $cy)
}
function ClickCreate { ClickButton 'Create File' 30 620 90 28 74 634 $true }
function ClickSave { ClickButton 'Save File Specifications' 180 620 170 28 264 634 $false }

# The four saved export names, read the proven way: select File 2, Tab to the two date fields, then the four name boxes follow.
function ReadNames {
  OpenM8
  AltTap 0x32; Start-Sleep -Milliseconds 1500; Front
  $found = $false; $v = ''
  for ($i = 1; $i -le 8; $i++) { Tap 0x09; Start-Sleep -Milliseconds 500; Front; $v = CopyField; if ($v -match '^\d\d/\d\d/\d\d$') { $found = $true; break } }
  if (-not $found) { Shot $script:M8 'names-no-date-field'; throw 'Could not find the date fields on the File 2 screen, so the saved export names were not read.' }
  $dates = @{ begin = $v }
  Tap 0x09; Start-Sleep -Milliseconds 500; Front; $dates.end = CopyField
  $names = @{}
  for ($n = 1; $n -le 4; $n++) { Tap 0x09; Start-Sleep -Milliseconds 500; Front; $names["$n"] = (CopyField).Trim() }
  Shot $script:M8 'names-read'
  [void](CloseM8 $script:M8)
  return @{ names = $names; saved_dates = $dates }
}

# ---- one export ------------------------------------------------------------------------------------------------------------
function ExportOne($e, $folder) {
  $res = [ordered]@{ ok = $false; message = ''; preset_name = $e.preset; file = ''; dialog = $false; confirmations = @() }
  $label = $e.type
  $path = Join-Path $folder $e.file
  $before = [datetime]::MinValue
  if (Test-Path $path) { $before = (Get-Item $path).LastWriteTimeUtc }
  OpenM8
  AltTap ([int](0x30 + [int]$e.slot)); Start-Sleep -Milliseconds 1500; Front
  Shot $script:M8 "$label-selected"
  if ($e.type -eq 'payments') {
    if (-not ($e.begin -match '^\d\d/\d\d/\d\d$' -and $e.end -match '^\d\d/\d\d/\d\d$')) { throw 'No transaction period was given.' }
    $found = $false; $v = ''
    for ($i = 1; $i -le 8; $i++) { Tap 0x09; Start-Sleep -Milliseconds 500; Front; $v = CopyField; if ($v -match '^\d\d/\d\d/\d\d$') { $found = $true; break } }
    if (-not $found) { Shot $script:M8 "$label-no-date-field"; throw 'Could not reach the Beginning Date field. Nothing was typed.' }
    $res.saved_dates = @{ begin = $v }
    TypeText $e.begin
    Tap 0x09; Start-Sleep -Milliseconds 500; Front
    $v = CopyField
    if ($v -notmatch '^\d\d/\d\d/\d\d$') { Shot $script:M8 "$label-no-end-field"; throw ("Expected the Ending Date field after Tab, but read '{0}'. Stopped." -f $v) }
    $res.saved_dates.end = $v
    TypeText $e.end
    Tap 0x09; Start-Sleep -Milliseconds 500        # leave the field so Frazer accepts the value
    ShiftTab; $gotE = CopyField
    ShiftTab; $gotB = CopyField
    Front
    $res.typed = @{ begin = $e.begin; end = $e.end }
    $res.readback = @{ begin = $gotB; end = $gotE }
    Shot $script:M8 "$label-dates"
    if ($gotB -ne $e.begin -or $gotE -ne $e.end) { throw ("The dates read back from Frazer ({0} to {1}) are not the period asked for ({2} to {3}). The export was NOT created." -f $gotB, $gotE, $e.begin, $e.end) }
  }
  $c = ConfirmPreset $e.type $e.slot
  $res.confirmations = $c.list
  if ($c.bad.Count) { Shot $script:M8 "$label-not-confirmed"; throw ("The screen did not show the expected settings ({0}). Create File was NOT clicked." -f ($c.bad -join ', ')) }
  $m8 = $script:M8
  $t0 = Get-Date
  ClickCreate
  $changed = $false; $dlg = $null; $nodlg = $false
  while (((Get-Date) - $t0).TotalSeconds -lt 150) {
    Start-Sleep -Milliseconds 400
    if (-not $changed -and (Test-Path $path) -and (Get-Item $path).LastWriteTimeUtc -gt $before) { $changed = $true; $res.seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1) }
    $dlg = Wins | Where-Object { $_.Title -like 'File Created*' } | Select-Object -First 1
    if ($changed -and $dlg) { break }
    # no message this time: Frazer closed the Export Data window by itself and the file has been still for 4 seconds
    if ($changed -and -not $dlg -and -not (Exists $m8) -and ((Get-Date).ToUniversalTime() - (Get-Item $path).LastWriteTimeUtc).TotalSeconds -gt 4) { $nodlg = $true; break }
  }
  if (-not ($changed -and ($dlg -or $nodlg))) {
    $mw = MainWin; if ($mw) { Shot $mw.H "$label-no-result" }
    $seen = (@(Wins | ForEach-Object { $_.Title -replace ' \(fz-.*$', '' }) -join '; ')
    if ($dlg -and -not $changed) { throw ("Frazer showed 'File Created' but {0} was not rewritten in {1}. Windows open: {2}" -f $e.file, $folder, $seen) }
    throw ("Frazer did not create {0} within the time allowed. Windows open: {1}" -f $e.file, $seen)
  }
  if ($dlg) {
    $res.dialog = $true
    [void][FzDrv]::ForceForeground($dlg.H); Start-Sleep -Milliseconds 500
    Shot $dlg.H "$label-file-created"
    if ([FzDrv]::GetForegroundWindow() -ne $dlg.H) { throw "The 'File Created' message could not be brought to the front, so it was not closed. Click OK on it." }
    Tap 0x0D; Start-Sleep -Milliseconds 1200
    if (Wins | Where-Object { $_.Title -like 'File Created*' }) { throw "The 'File Created' message did not close. Click OK on it." }
  }
  [void](CloseM8 $m8)
  $sz = -1
  for ($i = 0; $i -lt 30; $i++) { $now = (Get-Item $path).Length; if ($now -eq $sz -and $now -gt 0) { break }; $sz = $now; Start-Sleep -Seconds 2 }
  $it = Get-Item $path
  $res.ok = $true; $res.file = $it.FullName; $res.size_bytes = $it.Length; $res.written_local = $it.LastWriteTime.ToString('yyyy-MM-ddTHH:mm:ss')
  return $res
}

# After a failed step: close what this script opened, so the next export (or the operator) starts from the main screen
function Tidy {
  try {
    $d = Wins | Where-Object { $_.Title -like 'File Created*' } | Select-Object -First 1
    if ($d -and [FzDrv]::ForceForeground($d.H)) { Start-Sleep -Milliseconds 300; Tap 0x0D; Start-Sleep -Milliseconds 800 }
    foreach ($w in M8Wins) { [void](CloseM8 $w.H) }
  } catch { Note ("tidy: " + $_.Exception.Message) }
}

# ---- actions ---------------------------------------------------------------------------------------------------------------
$out = [ordered]@{ action = $Action; ok = $false; stopped = $null; started_local = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss') }
try {
  $req = $null
  if ($RequestPath) { $req = Get-Content -Raw -Path $RequestPath | ConvertFrom-Json }
  if (-not $OutDir -and $req -and $req.out_dir) { $OutDir = $req.out_dir }
  if ($OutDir) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

  if ($Action -eq 'Names') {
    foreach ($w in M8Wins) { [void](CloseM8 $w.H) }
    $n = ReadNames
    $out.names = $n.names; $out.saved_dates = $n.saved_dates; $out.ok = $true
  }

  if ($Action -eq 'Export') {
    $out.exports = [ordered]@{}
    foreach ($e in $req.exports) { $out.exports[$e.type] = [ordered]@{ ok = $false; message = 'Not attempted.' } }
    foreach ($w in M8Wins) { Note 'closing an Export Data window left open earlier'; [void](CloseM8 $w.H) }
    $n = ReadNames
    $out.names = $n.names
    foreach ($e in $req.exports) {
      $have = [string]$n.names["$($e.slot)"]
      if ($have.ToLower() -ne ([string]$e.preset).ToLower()) {
        $out.exports[$e.type] = [ordered]@{ ok = $false; preset_name = $have; message = ("File {0} in Frazer is named '{1}', not '{2}'. The saved export was renamed or the wrong slot is configured. Nothing was created." -f $e.slot, $have, $e.preset) }
        continue
      }
      try { $out.exports[$e.type] = ExportOne $e $req.folder }
      catch {
        $out.exports[$e.type] = [ordered]@{ ok = $false; preset_name = $have; message = $_.Exception.Message }
        Note ("{0}: {1}" -f $e.type, $_.Exception.Message)
        Tidy
      }
    }
    $out.ok = $true
  }

  if ($Action -eq 'Presets') {
    # 1. keep a picture of every saved export as it is now (each field tab), the names, and a fingerprint of each settings view
    foreach ($w in M8Wins) { [void](CloseM8 $w.H) }
    $before = ReadNames
    $out.names_before = $before.names; $out.saved_dates = $before.saved_dates
    $tabs = @(@('customers', 43), @('credit-application', 145), @('sales', 228), @('sales2', 278), @('vehicles', 334), @('insurance', 399), @('trade-ins', 468), @('write-off', 576))
    # Walk one saved export's screens. The fingerprint leaves out the name boxes (x 60-200 of the top-left block), which a rename changes.
    function Survey($s, $prefix) {
      $h = [ordered]@{}
      OpenM8
      AltTap ([int](0x30 + [int]$s.slot)); Start-Sleep -Milliseconds 1500; Front
      $r = M8Rect; [void][FzDrv]::SetCursorPos($r.L + 600, $r.T + 12); Start-Sleep -Milliseconds 500
      Shot $script:M8 ("{0}-file{1}" -f $prefix, $s.slot)
      $h['options'] = RegionHash $script:M8 205 60 811 210
      $h['file-type'] = RegionHash $script:M8 5 200 200 150
      if ($s.type -ne 'payments') {
        foreach ($t in $tabs) {
          Front; $r = M8Rect; [FzDrv]::Click($r.L + $t[1], $r.T + 341); Start-Sleep -Milliseconds 700
          [void][FzDrv]::SetCursorPos($r.L + 600, $r.T + 12); Start-Sleep -Milliseconds 400
          Shot $script:M8 ("{0}-file{1}-tab-{2}" -f $prefix, $s.slot, $t[0])
          $h[$t[0]] = RegionHash $script:M8 5 352 1006 262
        }
      } else { $h['payments-options'] = RegionHash $script:M8 5 270 1006 340 }
      [void](CloseM8 $script:M8)
      return $h
    }
    $fpBefore = @{}
    foreach ($s in $req.slots) { $fpBefore["$($s.slot)"] = Survey $s 'before' }
    $out.preserved = $true
    # 2. rename, one slot at a time, each verified before and after; nothing is saved unless the new name reads back exactly
    $out.renames = @()
    if (([FzDrv]::GetKeyState(0x14) -band 1) -ne 0) { throw 'Caps Lock is on. Turn it off and run this again; no name was changed.' }
    foreach ($s in $req.slots) {
      $cur = [string]$before.names["$($s.slot)"]
      $rn = [ordered]@{ slot = $s.slot; from = $cur; want = $s.want; result = '' }
      if (-not $s.want -or $cur -eq $s.want) { $rn.result = 'already named'; $out.renames += $rn; continue }
      foreach ($cand in @($s.want, $s.fallback)) {
        if (-not $cand) { continue }
        OpenM8
        AltTap ([int](0x30 + [int]$s.slot)); Start-Sleep -Milliseconds 1500; Front
        $r = M8Rect
        [FzDrv]::Click($r.L + 150, $r.T + $SlotY[[int]$s.slot] + 2); Start-Sleep -Milliseconds 500; Front
        ShiftTab; Tap 0x09; Start-Sleep -Milliseconds 500; Front        # leave the box and come back: the whole name is selected
        $have = (CopyField).Trim()
        if ($have -ne $cur) { Shot $script:M8 ("rename-file{0}-wrong-box" -f $s.slot); [void](CloseM8 $script:M8); throw ("Expected the File {0} name box to hold '{1}' but read '{2}'. Nothing was typed." -f $s.slot, $cur, $have) }
        TypeText $cand
        ShiftTab; Tap 0x09; Start-Sleep -Milliseconds 500; Front
        $got = (CopyField).Trim()
        Shot $script:M8 ("rename-file{0}-typed" -f $s.slot)
        if ($got -ne $cand) { $rn.result = ("'{0}' was not accepted (the box read '{1}'); not saved" -f $cand, $got); [void](CloseM8 $script:M8); continue }
        $c = ConfirmPreset $s.type $s.slot
        if ($c.bad.Count) { [void](CloseM8 $script:M8); throw ("File {0} no longer shows its saved settings ({1}). Not saved." -f $s.slot, ($c.bad -join ', ')) }
        ClickSave
        Start-Sleep -Milliseconds 2000
        for ($k = 0; $k -lt 3; $k++) {
          $d = Wins | Where-Object { $_.Title -notlike 'Frazer (*' -and $_.Title -notlike 'M-8*Export Data*' -and $_.W -lt 800 -and $_.Ht -lt 320 } | Select-Object -First 1
          if (-not $d) { break }
          Note ("after Save: message window '{0}' {1}x{2}; pressing Enter" -f $d.Title, $d.W, $d.Ht)
          [void][FzDrv]::ForceForeground($d.H); Start-Sleep -Milliseconds 400; Shot $d.H ("rename-file{0}-message{1}" -f $s.slot, $k)
          if ([FzDrv]::GetForegroundWindow() -ne $d.H) { break }
          Tap 0x0D; Start-Sleep -Milliseconds 1200
        }
        foreach ($w in M8Wins) { [void](CloseM8 $w.H) }
        $rn.result = 'typed and saved'; $rn.to = $cand
        break
      }
      $out.renames += $rn
    }
    # 3. read the names again from a fresh Export Data window: this, not the typing, says what is saved
    $after = ReadNames
    $out.names_after = $after.names
    # 4. look at every settings view again and compare with the pictures taken before: a rename must change nothing else
    $changedViews = @()
    foreach ($s in $req.slots) {
      $now = Survey $s 'after'
      foreach ($k in $fpBefore["$($s.slot)"].Keys) { if ($fpBefore["$($s.slot)"][$k] -ne $now[$k]) { $changedViews += ("File {0}: {1}" -f $s.slot, $k) } }
    }
    $out.settings_views_compared = @($fpBefore.Values | ForEach-Object { $_.Count } | Measure-Object -Sum).Sum
    $out.settings_changed = $changedViews
    $out.settings_unchanged = ($changedViews.Count -eq 0)
    $out.ok = $true
  }
} catch {
  $out.stopped = $_.Exception.Message
  Note ("STOPPED: " + $_.Exception.Message)
  if ($Action -ne 'Presets') { Tidy }
}
try { $out.windows_at_end = @(Wins | ForEach-Object { ($_.Title -replace ' \(fz-.*$', '') }) } catch {}
WriteResult $out
if ($out.stopped) { exit 2 } else { exit 0 }
