# Builds graftsnip.ico and creates "GraftSnip" shortcuts in the Start menu and on the desktop (current user only).
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$script = Join-Path $root 'GraftSnip.pyw'
$ico = Join-Path $root 'graftsnip.ico'
if (-not (Test-Path $pythonw)) { throw "Virtual environment missing: $pythonw" }

& (Join-Path $root '.venv\Scripts\python.exe') $script --write-icon $ico
if ($LASTEXITCODE -ne 0) { throw "Couldn't write $ico" }
$programs = [Environment]::GetFolderPath('Programs')
$desktop = [Environment]::GetFolderPath('Desktop')

# shortcuts from before the rebrand
foreach ($old in @((Join-Path $programs 'SnipMark.lnk'), (Join-Path $desktop 'SnipMark.lnk'))) {
    if (Test-Path $old) { Remove-Item -LiteralPath $old; Write-Output "Removed $old" }
}

$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in @((Join-Path $programs 'GraftSnip.lnk'), (Join-Path $desktop 'GraftSnip.lnk'),
                   (Join-Path $root 'GraftSnip.lnk'))) {
    # delete first: Windows paths ignore case, so saving over "Graftsnip.lnk" would keep the old spelling
    if (Test-Path $lnk) { Remove-Item -LiteralPath $lnk }
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $pythonw
    $s.Arguments = "`"$script`""
    $s.WorkingDirectory = $root
    $s.IconLocation = "$ico,0"
    $s.Description = 'GraftSnip - snip, point and label'
    $s.Save()
    Write-Output "Created $lnk"
}
