# Builds the GraftSnip Windows installer.
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build.ps1
# Needs: py (Python launcher) and Inno Setup 6 (winget install JRSoftware.InnoSetup).
# Output: packaging\out\GraftSnip-<version>-windows-x64-setup.exe
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$root = Split-Path $here -Parent
$work = Join-Path $here 'work'
$venv = Join-Path $here '.buildvenv'
$py = Join-Path $venv 'Scripts\python.exe'

# 1. A separate build environment, so the app's own .venv stays as it is.
if (-not (Test-Path $py)) {
    & (Join-Path $root '.venv\Scripts\python.exe') -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the build environment' }
}
& $py -m pip install --quiet --disable-pip-version-check -r (Join-Path $root 'requirements.txt') pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

# 2. Icon (gitignored, so make it if missing) and version.
$ico = Join-Path $root 'graftsnip.ico'
if (-not (Test-Path $ico)) { & $py (Join-Path $root 'GraftSnip.pyw') --write-icon $ico }
$version = (& $py -c "import sys; sys.path.insert(0, r'$root'); from graftsnip.settings import __version__; print(__version__)").Trim()
if (-not $version) { throw 'Could not read the version' }
Write-Output "GraftSnip $version"

# 3. Freeze the app into work\dist\GraftSnip (one folder: starts faster than one file).
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
& $py -m PyInstaller --noconfirm --clean --windowed `
    --name GraftSnip `
    --icon $ico `
    --add-data "$(Join-Path $root 'graftsnip\assets');graftsnip\assets" `
    --add-data "$(Join-Path $root 'graftsnip\ocr.ps1');graftsnip" `
    --distpath (Join-Path $work 'dist') `
    --workpath (Join-Path $work 'build') `
    --specpath $work `
    (Join-Path $root 'GraftSnip.pyw')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

# 4. Wrap it in an installer.
$iscc = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 not found (winget install JRSoftware.InnoSetup)' }
& $iscc /Qp "/DAppVersion=$version" (Join-Path $here 'graftsnip.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
Get-ChildItem (Join-Path $here 'out') -Filter '*.exe' | ForEach-Object { '{0}  {1:N1} MB' -f $_.FullName, ($_.Length / 1MB) }
