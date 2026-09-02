<#
.SYNOPSIS
    Builds a self-contained, relocatable Windows bundle of the Squid control software.

.DESCRIPTION
    The repo on its own is NOT runnable on another machine: it has no venv, no
    pinned interpreter, and every dependency lives in a user-scoped Python 3.12
    install outside the repo. This script produces a folder (and zip) that
    carries the interpreter and all dependencies inside it, so it can be
    unzipped anywhere and launched by double-clicking Squid.cmd.

    Approach: copy the existing, known-good Python 3.12 tree rather than
    resolving a fresh environment from requirements. A CPython install on
    Windows finds its Lib/DLLs relative to sys.executable, so it relocates.
    This preserves the exact working dependency set, which has drifted from
    software/setup_22.04.sh (that script pins numpy<2 and napari==0.5.4; the
    machine actually runs numpy 2.4.4 and napari 0.5.6).

    NOT bundled: the Daheng Galaxy SDK. software/control/gxipy/gxwrapper.py
    calls WinDLL('GxIAPI.dll'), which resolves off the system PATH, and the SDK
    also installs a kernel-mode USB driver. The target machine must have it
    installed -- run CHECK-DRIVERS.cmd there to confirm.

.EXAMPLE
    .\build_portable_windows.ps1
    .\build_portable_windows.ps1 -IncludeFirmware -SkipZip
#>
[CmdletBinding()]
param(
    # The known-good interpreter to clone into the bundle.
    [string] $SourcePython = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'),

    # Where the bundle is assembled. Its leaf name becomes the folder inside the zip.
    [string] $StagingDir,

    # Where the .zip is written. Defaults to the repo's parent directory.
    [string] $OutDir,

    [switch] $IncludeFirmware,   # +50 MB, PlatformIO sources
    [switch] $IncludeGit,        # +365 MB, makes the target folder git-pullable
    [switch] $IncludeTests,      # +6.4 MB
    [switch] $SkipZip,           # stop after staging
    [switch] $KeepStaging        # do not delete the staging dir afterwards
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- helpers ---

$script:StepNumber = 0
function Write-Step([string] $Message) {
    $script:StepNumber++
    Write-Host ''
    Write-Host ("[{0}] {1}" -f $script:StepNumber, $Message) -ForegroundColor Cyan
}

function Write-Note([string] $Message) { Write-Host "      $Message" -ForegroundColor DarkGray }
function Write-Warn([string] $Message) { Write-Host "      WARNING: $Message" -ForegroundColor Yellow }

function Get-DirSizeMB([string] $Path) {
    if (-not (Test-Path $Path)) { return 0 }
    $sum = (Get-ChildItem $Path -Recurse -File -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return 0 }
    return [math]::Round($sum / 1MB, 1)
}

# Robocopy returns 0-7 for success (1 = files copied, 3 = copied + extras, etc.)
# and >=8 for genuine failure. It also leaves a nonzero $LASTEXITCODE that would
# trip up later checks, so normalise it.
function Invoke-Robocopy {
    param(
        [Parameter(Mandatory=$true)] [string] $Source,
        [Parameter(Mandatory=$true)] [string] $Destination,
        [string[]] $ExcludeDirs  = @(),
        [string[]] $ExcludeFiles = @()
    )
    $rcArgs = @(
        $Source.TrimEnd('\'),
        $Destination.TrimEnd('\'),
        '/E',            # include subdirs, including empty ones
        '/COPY:DAT',     # data, attributes, timestamps -- no ACLs, which would
                         # carry this machine's SIDs to the target
        '/DCOPY:DAT',
        '/R:1', '/W:1',  # do not retry for 30s x 1M on a locked file
        '/MT:16',
        '/NFL', '/NDL', '/NJH', '/NJS', '/NP'
    )
    foreach ($d in $ExcludeDirs)  { $rcArgs += '/XD'; $rcArgs += $d }
    foreach ($f in $ExcludeFiles) { $rcArgs += '/XF'; $rcArgs += $f }

    & robocopy.exe @rcArgs | Out-Null
    $code = $LASTEXITCODE
    $global:LASTEXITCODE = 0
    if ($code -ge 8) {
        throw "robocopy failed (exit $code) copying '$Source' -> '$Destination'"
    }
}

function Write-TextFile([string] $Path, [string] $Content, [switch] $Ascii) {
    # Deliberately BOM-less. configparser reads configuration_Squid+.ini as
    # plain utf-8, so a BOM would turn the first section header into
    # "\ufeff[section]" and raise MissingSectionHeaderError at startup.
    if ($Ascii) { $encoding = New-Object System.Text.ASCIIEncoding }
    else        { $encoding = New-Object System.Text.UTF8Encoding($false) }
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

$NL = [Environment]::NewLine

# ------------------------------------------------------------------ paths ---

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not (Test-Path (Join-Path $RepoRoot 'software\main_hcs.py'))) {
    throw "Could not locate the repo root from '$PSScriptRoot' (no software\main_hcs.py at '$RepoRoot')."
}

if (-not $StagingDir) { $StagingDir = Join-Path $env:TEMP 'squid-portable-build\Squid-Portable' }
if (-not $OutDir)     { $OutDir     = Split-Path $RepoRoot -Parent }

$SourcePythonExe = Join-Path $SourcePython 'python.exe'
$RuntimeDir      = Join-Path $StagingDir 'runtime\python312'
$StagedPythonExe = Join-Path $RuntimeDir 'python.exe'
$StagedSoftware  = Join-Path $StagingDir 'software'

Write-Host ''
Write-Host '=== Squid portable Windows build ===' -ForegroundColor Green
Write-Note "repo     : $RepoRoot"
Write-Note "python   : $SourcePython"
Write-Note "staging  : $StagingDir"
Write-Note "output   : $OutDir"

# ------------------------------------------- 1. validate source interpreter ---

Write-Step 'Validating the source interpreter'

if (-not (Test-Path $SourcePythonExe)) {
    throw "No python.exe at '$SourcePythonExe'. Pass -SourcePython <dir> to point at the working Python 3.12 install."
}

$pyVersion = (& $SourcePythonExe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
Write-Note "version  : $pyVersion"
if (-not $pyVersion.StartsWith('3.12')) {
    Write-Warn "expected Python 3.12.x, found $pyVersion -- the app is only known to work on 3.12."
}

# Anything installed into per-user site lives outside $SourcePython and would be
# silently missing from the bundle.
$userSite = (& $SourcePythonExe -c "import site; print(site.getusersitepackages())").Trim()
if (Test-Path $userSite) {
    $strays = @(Get-ChildItem $userSite -ErrorAction SilentlyContinue)
    if ($strays.Count -gt 0) {
        Write-Warn "per-user site-packages is not empty and will NOT be bundled: $userSite"
        $strays | ForEach-Object { Write-Warn "  $($_.Name)" }
    }
}

# Absolute paths baked into .pth files would break after relocation. Console
# script shims in Scripts\ also embed the absolute interpreter path, but the
# launcher never invokes one -- it runs python.exe main_hcs.py directly.
$sitePackages = Join-Path $SourcePython 'Lib\site-packages'
$badPth = @(Get-ChildItem $sitePackages -Filter '*.pth' -ErrorAction SilentlyContinue |
            Where-Object { (Get-Content $_.FullName -Raw) -match '[A-Za-z]:\\' })
if ($badPth.Count -gt 0) {
    Write-Warn 'these .pth files contain absolute paths and may not relocate:'
    $badPth | ForEach-Object { Write-Warn "  $($_.Name)" }
}

# --------------------------------------------- 2. record the dependency set ---

Write-Step 'Recording the dependency set (requirements-windows.txt)'

$reqHeader = @'
# Pinned dependency set for the Windows build of Squid.
#
# Generated by `pip freeze` against the known-good Python 3.12 install at
#   C:\Users\<user>\AppData\Local\Programs\Python\Python312
# This is the RECORD of the working environment, not the build input --
# software/tools/build_portable_windows.ps1 copies that interpreter wholesale.
# Use this file only to rebuild an environment from scratch.
#
# NOTE: this set has drifted from software/setup_22.04.sh (which pins numpy<2
# and napari==0.5.4). The values below are what actually runs.
#
# Referenced by config but deliberately NOT installed, matching the working
# machine: zarr, ndv, mcp, pyvisa, dask_image, ome_zarr, aicsimageio, basicpy,
# hidapi, pipython.
#
'@

$frozen = & $SourcePythonExe -m pip freeze
$reqPath = Join-Path $RepoRoot 'requirements-windows.txt'
Write-TextFile -Path $reqPath -Content ($reqHeader + $NL + ($frozen -join $NL) + $NL)
Write-Note "$($frozen.Count) packages -> $reqPath"

# ------------------------------------------------------- 3. reset staging ---

Write-Step 'Preparing the staging directory'

if (Test-Path $StagingDir) {
    Write-Note 'removing previous staging directory'
    Remove-Item $StagingDir -Recurse -Force
}
New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

# ----------------------------------------------- 4. copy the Python runtime ---

Write-Step 'Copying the Python runtime'

Invoke-Robocopy -Source $SourcePython -Destination $RuntimeDir -ExcludeDirs @(
    '__pycache__',
    (Join-Path $SourcePython 'Doc'),        # 51 MB of CHM/HTML docs
    (Join-Path $SourcePython 'Lib\test')    # 30 MB CPython test suite
)
if (-not (Test-Path $StagedPythonExe)) { throw "Runtime copy produced no python.exe at '$StagedPythonExe'." }
Write-Note "runtime  : $(Get-DirSizeMB $RuntimeDir) MB"

# -------------------------------------------------------- 5. copy the app ---

Write-Step 'Copying the application'

$driversDir = Join-Path $RepoRoot 'software\drivers and libraries'
$toupcamWin = Join-Path $driversDir 'toupcam\windows'

$excludeDirs = @(
    '__pycache__', '.pytest_cache', '.idea', '.mypy_cache',
    (Join-Path $RepoRoot 'WalkThrough Videos'),              # 2.4 GB
    (Join-Path $RepoRoot 'Screenshots for troubleshooting'), # 16 MB
    (Join-Path $RepoRoot '.github'),                         # CI, meaningless on the target

    # Linux/macOS vendor payloads. The Windows Daheng side comes from the
    # system SDK, not from here; TUCam.py hardcodes a ./lib/x64/TUCam.dll that
    # does not exist in this repo and is unused by this config.
    (Join-Path $driversDir 'daheng camera'),   # 53 MB, Linux installers only
    (Join-Path $driversDir 'tucsen'),          # 23 MB, .so only
    (Join-Path $driversDir 'toupcam\linux'),   # 225 MB
    (Join-Path $driversDir 'toupcam\mac'),     # 39 MB

    # Toupcam Windows variants for architectures this build does not target.
    # KEEP windows\x64 -- control/toupcam.py loads
    # ../drivers and libraries/toupcam/windows/x64/toupcam.dll relative to
    # __file__, and that is the main camera (ITR3CMOS26000KMA).
    # KEEP windows\drivers -- 666 KB of .inf/.sys, handy for a driver repair.
    (Join-Path $toupcamWin 'arm64'),           # 17 MB
    (Join-Path $toupcamWin 'winrt'),           # 59 MB
    (Join-Path $toupcamWin 'x86')              # 19 MB
)

if (-not $IncludeGit)      { $excludeDirs += (Join-Path $RepoRoot '.git') }
if (-not $IncludeFirmware) { $excludeDirs += (Join-Path $RepoRoot 'firmware') }
if (-not $IncludeTests)    { $excludeDirs += (Join-Path $RepoRoot 'software\tests') }

# Working notes and repo plumbing that would only confuse someone opening the
# bundle on the target machine. README-TARGET.md is what they should read.
$excludeFiles = @(
    '*.pyc',
    '*.patch',
    '.pre-commit-config.yaml',
    '.gitmodules',
    'Laser AF Regression Plan.md',
    'Joystick Button Handling in Squid Firmware.md'
)

Invoke-Robocopy -Source $RepoRoot -Destination $StagingDir `
                -ExcludeDirs $excludeDirs `
                -ExcludeFiles $excludeFiles

$toupcamDll = Join-Path $StagedSoftware 'drivers and libraries\toupcam\windows\x64\toupcam.dll'
if (-not (Test-Path $toupcamDll)) { throw "Main camera DLL missing from the bundle: '$toupcamDll'." }

Write-Note "app      : $(Get-DirSizeMB $StagedSoftware) MB"

# Machine state that is git-ignored, so it exists only as working-tree files.
# Zipping the folder, rather than cloning, is what carries it across.
foreach ($item in @('configuration_Squid+.ini', 'cache', 'user_profiles',
                    'machine_configs\filter_wheels.yaml',
                    'machine_configs\illumination_channel_config.yaml')) {
    $p = Join-Path $StagedSoftware $item
    if (Test-Path $p) { Write-Note "carried  : software\$item" }
    else              { Write-Warn  "missing  : software\$item" }
}

# ------------------------------------------- 6. make the save path portable ---

Write-Step 'Rewriting the machine-specific save path'

$iniPath = Join-Path $StagedSoftware 'configuration_Squid+.ini'
if (Test-Path $iniPath) {
    $lines   = Get-Content $iniPath
    $current = ($lines | Where-Object { $_ -match '^\s*default_saving_path\s*=' } | Select-Object -First 1)

    # control/_def.py does:
    #     if not DEFAULT_SAVING_PATH.startswith(str(Path.home())):
    #         DEFAULT_SAVING_PATH = str(Path.home() / DEFAULT_SAVING_PATH.strip("/").strip("\\"))
    # .strip() does not remove a "C:" drive prefix, so a foreign absolute path
    # is concatenated into garbage like C:\Users\other\C:\Users\someone\Downloads
    # rather than falling back. A bare relative value resolves correctly.
    $patched = $lines -replace '^(\s*default_saving_path\s*=\s*).*$', '${1}Downloads'
    Write-TextFile -Path $iniPath -Content (($patched -join $NL) + $NL)

    if ($current) { Write-Note "was      : $($current.Trim())" }
    Write-Note 'now      : default_saving_path = Downloads   (resolves to <target user home>\Downloads)'
} else {
    Write-Warn 'no configuration_Squid+.ini in the bundle -- the target will have no active config.'
}

# ------------------------------------------------- 7. write the launchers ---

Write-Step 'Writing launchers'

# Shared preamble. Two things it must guarantee:
#   * CWD is software\ -- control/_def.py reads cache/config_file_path.txt and
#     globs ./configuration*.ini, and main_hcs.py loads icon/cephla_logo.ico,
#     all relative to the working directory.
#   * the bundled interpreter is used regardless of PATH. On the build machine
#     C:\Python314\python.exe shadows 3.12 and has none of the dependencies.
$preamble = @'
setlocal
set "HERE=%~dp0"
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"

rem Daheng Galaxy SDK, for the laser-AF camera (MER2-630-60U3M).
rem control/gxipy/gxwrapper.py loads GxIAPI.dll by bare name off the system
rem PATH. The vendor installer normally adds these; prepending them here makes
rem the launcher work even before a post-install reboot.
set "PATH=C:\Program Files\Daheng Imaging\GalaxySDK\APIDll\Win64;C:\Program Files\Daheng Imaging\GalaxySDK\GenICam\bin\Win64_x64;%PATH%"

if not exist "%HERE%runtime\python312\python.exe" (
    echo ERROR: bundled Python runtime not found at "%HERE%runtime\python312".
    echo Did the whole folder get copied, or just part of it?
    pause
    exit /b 1
)

cd /d "%HERE%software"
'@

$squidCmdHead = @'
@echo off
rem Normal launch. No console window; the app logs to %LOCALAPPDATA%.
rem If nothing appears on screen, run Squid-Console.cmd instead to see why.
'@
$squidCmdTail = @'

start "" "%HERE%runtime\python312\pythonw.exe" main_hcs.py
'@
Write-TextFile -Ascii -Path (Join-Path $StagingDir 'Squid.cmd') `
               -Content ($squidCmdHead + $NL + $preamble + $squidCmdTail)

$consoleHead = @'
@echo off
rem Same as Squid.cmd but keeps a console so import errors, missing DLLs and
rem tracebacks are visible. Use this for the first launch on a new machine --
rem pythonw.exe would fail silently.
'@
$consoleTail = @'

"%HERE%runtime\python312\python.exe" main_hcs.py
echo.
echo --- Squid exited with code %ERRORLEVEL% ---
pause
'@
Write-TextFile -Ascii -Path (Join-Path $StagingDir 'Squid-Console.cmd') `
               -Content ($consoleHead + $NL + $preamble + $consoleTail)

$checkHead = @'
@echo off
rem Confirms the bundled Python works and both cameras' libraries load.
rem Run this first if the app misbehaves -- it separates a driver problem from
rem a Python problem in about ten seconds.
'@
$checkTail = @'

"%HERE%runtime\python312\python.exe" tools\check_drivers.py
echo.
pause
'@
Write-TextFile -Ascii -Path (Join-Path $StagingDir 'CHECK-DRIVERS.cmd') `
               -Content ($checkHead + $NL + $preamble + $checkTail)

$shortcutCmd = @'
@echo off
rem Creates a Desktop shortcut to Squid.cmd with the Cephla icon.
setlocal
set "HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "$l = $s.CreateShortcut([IO.Path]::Combine($s.SpecialFolders('Desktop'), 'Squid.lnk'));" ^
  "$l.TargetPath = '%HERE%Squid.cmd';" ^
  "$l.WorkingDirectory = '%HERE%';" ^
  "$l.IconLocation = '%HERE%software\icon\cephla_logo.ico,0';" ^
  "$l.WindowStyle = 7;" ^
  "$l.Save();" ^
  "Write-Host 'Created Squid.lnk on the Desktop.'"
pause
'@
Write-TextFile -Ascii -Path (Join-Path $StagingDir 'Make-Desktop-Shortcut.cmd') -Content $shortcutCmd

Write-Note 'Squid.cmd, Squid-Console.cmd, CHECK-DRIVERS.cmd, Make-Desktop-Shortcut.cmd'

# ------------------------------------------------------- 8. write a README ---

Write-Step 'Writing README-TARGET.md'

$readme = @'
# Squid -- portable Windows bundle

Self-contained. The Python interpreter and every dependency are inside
`runtime\python312\`; nothing needs to be installed to run the app.

## Running it

1. Unzip anywhere. A short path such as `C:\Squid` is safest -- some
   dependencies still trip over very long paths.
2. Run **`CHECK-DRIVERS.cmd`** once. Every line should print OK.
3. Run **`Squid-Console.cmd`** for the first launch, so any error is visible.
   Once it works, use `Squid.cmd` (no console) day to day.
4. Optionally run `Make-Desktop-Shortcut.cmd` for a Desktop icon.

Do not move `Squid.cmd` out of this folder -- it locates the runtime and the
`software\` working directory relative to itself.

## Requirement that is NOT in this bundle

The **Daheng Galaxy SDK** must be installed on this machine. It provides
`GxIAPI.dll` for the laser-AF camera (MER2-630-60U3M) plus a kernel-mode USB
driver, and it is loaded by name off the system PATH, so it cannot travel in a
zip. If `CHECK-DRIVERS.cmd` reports the Daheng library missing, install the SDK
and **reboot** -- a missing reboot after install is the usual cause.

The main camera (Toupcam ITR3CMOS26000KMA) needs its USB driver present too,
but its DLL ships inside this bundle. `.inf`/`.sys` files for a driver repair
are under `software\drivers and libraries\toupcam\windows\drivers\`.

## Where things go

- Images: `<your user folder>\Downloads` by default, changeable in the GUI.
- Logs: `%LOCALAPPDATA%\cephla\squid\Logs`.
- Config: `software\configuration_Squid+.ini`, `software\cache\`,
  `software\machine_configs\`.

## Calibration carried over from the source machine

`software\user_profiles\default\` holds the channel configs and laser-AF
calibration from the machine this bundle was built on. **If this bundle is
driving a different physical microscope, those values are wrong** -- redo the
laser-AF calibration and check the illumination channel settings before
trusting an acquisition.

## Updating

This bundle has no `.git` unless it was built with `-IncludeGit`, so it cannot
be updated with `git pull`. Rebuild on the source machine and re-copy.
'@
Write-TextFile -Path (Join-Path $StagingDir 'README-TARGET.md') -Content $readme

# ---------------------------------------------------------- 9. smoke test ---

Write-Step 'Smoke-testing the staged runtime'

$saved = @{}
foreach ($name in 'PYTHONHOME', 'PYTHONPATH', 'PYTHONNOUSERSITE') {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    [Environment]::SetEnvironmentVariable('PYTHONHOME',       $null, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONPATH',       $null, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONNOUSERSITE', '1',   'Process')

    Push-Location $StagedSoftware
    try {
        & $StagedPythonExe (Join-Path $StagedSoftware 'tools\check_drivers.py') --imports-only
        if ($LASTEXITCODE -ne 0) {
            throw 'Smoke test failed: the staged runtime could not import the application dependencies.'
        }

        # The staged interpreter must resolve its own stdlib, not reach back into
        # the source install. Compared against the source prefix rather than
        # against $RuntimeDir: %TEMP% is often the 8.3 short form, so a literal
        # path-string match would false-fail on a perfectly good bundle.
        $srcPrefix    = (& $SourcePythonExe -c "import sys; print(sys.prefix)").Trim()
        $stagedPrefix = (& $StagedPythonExe -c "import sys; print(sys.prefix)").Trim()
        if ($stagedPrefix -eq $srcPrefix) {
            throw "Staged interpreter still reports sys.prefix='$stagedPrefix' (the source install). It is not self-contained."
        }
        Write-Note "staged runtime is self-contained (sys.prefix -> $stagedPrefix)"
    } finally {
        Pop-Location
    }
} finally {
    foreach ($name in @($saved.Keys)) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
}

# --------------------------------------------------------------- 10. zip ---

$stagedMB = Get-DirSizeMB $StagingDir
Write-Host ''
Write-Host ("Staged: {0} MB at {1}" -f $stagedMB, $StagingDir) -ForegroundColor Green

if ($SkipZip) {
    Write-Host 'Skipping the zip (-SkipZip).' -ForegroundColor Yellow
    return
}

Write-Step 'Creating the zip'

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$zipPath = Join-Path $OutDir ("Squid-Portable-{0}.zip" -f (Get-Date -Format 'yyyyMMdd'))
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

$sevenZip = @(
    (Join-Path $env:ProgramFiles '7-Zip\7z.exe'),
    (Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($sevenZip) {
    Write-Note "using $sevenZip"
    & $sevenZip a -tzip -mx=5 -bso0 -bsp0 $zipPath $StagingDir | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "7-Zip failed with exit code $LASTEXITCODE." }
} else {
    # Compress-Archive is unusably slow at this size; go straight to the API.
    Write-Note '7-Zip not found, using System.IO.Compression (slower)'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $StagingDir, $zipPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $true)   # includeBaseDirectory, so the zip has a single top-level folder
}

$zipMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)

if (-not $KeepStaging) {
    Write-Note 'removing staging directory'
    Remove-Item $StagingDir -Recurse -Force
}

Write-Host ''
Write-Host '=== Done ===' -ForegroundColor Green
Write-Host ("  {0}  ({1} MB, from {2} MB staged)" -f $zipPath, $zipMB, $stagedMB)
Write-Host ''
Write-Host '  On the target machine: unzip, run CHECK-DRIVERS.cmd, then Squid-Console.cmd.'
Write-Host ''
