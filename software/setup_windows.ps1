#Requires -Version 5.1
<#
.SYNOPSIS
    Setup script for Squid on Windows.
.DESCRIPTION
    Verifies prerequisites, clones the repository if needed, installs Python
    dependencies, and creates a desktop shortcut.

    This script targets Python 3.12 and napari 0.7:

      1. Python 3.12 is required exactly. napari 0.7 supports 3.10-3.14, but
         3.12 is the newest version with wheels for every dependency here
         (notably PyQt5 and hidapi), and pinning one interpreter keeps every
         Windows install reproducible.

      2. napari is installed as napari[pyqt5]==0.7.1, NOT napari[all]. As of
         napari 0.7 the "all"/"qt" extras resolve to PyQt6, but Squid's GUI is
         PyQt5 (control/widgets.py, control/gui_hcs.py via qtpy). Installing
         both bindings makes qtpy's choice non-deterministic and crashes the
         GUI, so the PyQt5 extra is requested explicitly.

      3. numpy is not capped at <2. The <2 cap on setup_22.04.sh exists for
         aicsimageio, which is not installed here (see 4).

      4. aicsimageio and basicpy are NOT installed. Their only importer is
         control/stitcher.py, which nothing in the codebase imports (the
         active stitcher is tools/stitcher.py, an ImageJ/Fiji-based path).
         Both are hard to build on Windows, so dropping the dead dependency
         de-risks the install. If control/stitcher.py is ever revived, add
         them back here.

      5. Packages the Ubuntu scripts get from apt (pyqtgraph, PyQt5) are
         installed from PyPI here, since Windows has no system package
         manager providing them.
.PARAMETER RepoPath
    Path where Squid repository should be cloned. Defaults to Desktop\Squid.
#>

param(
    [string]$RepoPath = "$env:USERPROFILE\Desktop\Squid"
)

$ErrorActionPreference = "Stop"

# Required Python version. napari 0.7 also runs on 3.10/3.11/3.13/3.14, but we
# pin one version so every Windows machine ends up with the same environment.
$REQUIRED_PYTHON_MAJOR = 3
$REQUIRED_PYTHON_MINOR = 12
$REQUIRED_PYTHON = "$REQUIRED_PYTHON_MAJOR.$REQUIRED_PYTHON_MINOR"

Write-Host "Using SQUID_REPO_PATH='$RepoPath'"

$SQUID_REPO_HTTP = "https://github.com/Cephla-Lab/Squid.git"
$SQUID_SOFTWARE_ROOT = Join-Path $RepoPath "software"
$SQUID_REPO_PATH_PARENT = Split-Path $RepoPath -Parent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# PowerShell's $ErrorActionPreference does not apply to native executables:
# a failing python/pip/git call only sets $LASTEXITCODE, and the script would
# otherwise sail past a broken install and still create a desktop shortcut.
function Assert-LastExitCode {
    param([string]$What)

    if ($LASTEXITCODE -ne 0) {
        throw "$What failed with exit code $LASTEXITCODE"
    }
}

# Probe a candidate interpreter. Returns a hashtable with its version, bitness
# and real executable path, or $null if the candidate cannot be run at all.
function Get-PythonInfo {
    param(
        [string]$Exe,
        [string[]]$Prefix = @()
    )

    $probe = "import sys; print(sys.version_info[0]); print(sys.version_info[1]); " +
             "print(8 * (sys.maxsize > 2**32)); print(sys.executable)"

    # A wrong-version py launcher, or the Microsoft Store python.exe stub,
    # writes to stderr and exits non-zero. Under $ErrorActionPreference =
    # "Stop" Windows PowerShell can turn that into a terminating
    # NativeCommandError, so probing runs with the preference relaxed and we
    # judge the candidate on its exit code instead.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Exe @Prefix -c $probe 2>$null
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($LASTEXITCODE -ne 0 -or $null -eq $output -or $output.Count -lt 4) {
        return $null
    }

    return @{
        Major      = [int]$output[0]
        Minor      = [int]$output[1]
        Bits       = [int]$output[2]
        Executable = $output[3]
    }
}

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

# Find a Python 3.12 interpreter. The py launcher is tried first because it can
# select an exact version even when a different Python owns the PATH.
$candidates = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $candidates += @{ Exe = "py"; Prefix = @("-$REQUIRED_PYTHON") }
}
foreach ($name in @("python$REQUIRED_PYTHON", "python3", "python")) {
    if (Get-Command $name -ErrorAction SilentlyContinue) {
        $candidates += @{ Exe = $name; Prefix = @() }
    }
}

$PythonExe = $null
$foundVersions = @()
foreach ($candidate in $candidates) {
    $info = Get-PythonInfo -Exe $candidate.Exe -Prefix $candidate.Prefix
    if ($null -eq $info) {
        continue
    }

    $version = "$($info.Major).$($info.Minor)"
    $foundVersions += "$version ($($info.Executable))"

    if ($info.Major -ne $REQUIRED_PYTHON_MAJOR -or $info.Minor -ne $REQUIRED_PYTHON_MINOR) {
        continue
    }
    if ($info.Bits -ne 64) {
        Write-Warning "Ignoring 32-bit Python at $($info.Executable); Squid needs 64-bit Python."
        continue
    }

    # Use sys.executable rather than the launcher so every later call (pip,
    # the desktop shortcut) is pinned to this exact interpreter.
    $PythonExe = $info.Executable
    break
}

if ($null -eq $PythonExe) {
    $detail = if ($foundVersions.Count -gt 0) {
        "Found instead: " + ($foundVersions -join ", ") + "."
    } else {
        "No Python interpreter was found on PATH."
    }
    Write-Error ("64-bit Python $REQUIRED_PYTHON is required but was not found. $detail`n" +
                 "Install it from https://www.python.org/downloads/release/python-3120/ " +
                 "(check 'Add python.exe to PATH' in the installer), then re-run this script.")
    exit 1
}
Write-Host "Using Python $REQUIRED_PYTHON at $PythonExe"

# Check if git is installed
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "Git is not installed or not in PATH. Please install Git from https://git-scm.com"
    exit 1
}
Write-Host "Found $(git --version)"

# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

# Clone the repo if we don't already have it
if (-not (Test-Path $SQUID_REPO_PATH_PARENT)) {
    New-Item -ItemType Directory -Path $SQUID_REPO_PATH_PARENT -Force | Out-Null
}

if (-not (Test-Path $RepoPath)) {
    Write-Host "Cloning Squid repository..."
    # --recurse-submodules: control/ndviewer_light and fluidics_v2 are
    # submodules, and the GUI fails to start without them.
    git clone --recurse-submodules $SQUID_REPO_HTTP $RepoPath
    Assert-LastExitCode "git clone"
} else {
    $currentHead = git -C $RepoPath rev-parse HEAD
    Assert-LastExitCode "git rev-parse"
    Write-Host "Using existing repo at '$RepoPath' at HEAD=$currentHead"
    Write-Host "Updating submodules..."
    git -C $RepoPath submodule update --init --recursive
    Assert-LastExitCode "git submodule update"
}

# Create cache directory
$cacheDir = Join-Path $SQUID_SOFTWARE_ROOT "cache"
if (-not (Test-Path $cacheDir)) {
    New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
}

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------

# Grouped for readability, but installed in a single pip call so the resolver
# sees every constraint at once. Splitting the install lets a later call pull a
# conflicting version of something an earlier call already placed - which is
# exactly how a stray PyQt6 ends up beside PyQt5.
$packages = @(
    # Core runtime
    "qtpy",
    "pyserial",
    "pandas",
    "imageio",
    "crc==1.3.0",
    "lxml",
    "lxml_html_clean",
    "numpy",
    "tifffile",
    "scipy",
    "psutil",
    "platformdirs",
    "pyyaml",
    "filelock",
    "gitpython",
    "matplotlib",
    "pydantic_xml",
    # Windows-only readline replacement (control/console.py)
    "pyreadline3",
    # GUI. The pyqt5 extra is required - see the note at the top of this file.
    "napari[pyqt5]==0.7.1",
    "pyqtgraph",
    "ndv",
    # Image processing
    "opencv-python-headless",
    "opencv-contrib-python-headless",
    "scikit-image",
    "dask_image",
    "ome_zarr",
    # Optional at import time (control/core/zarr_writer.py imports it lazily),
    # but required for the ZARR_V3 file_saving_option to work at all.
    "tensorstore",
    # Hardware / instrument I/O
    "pyvisa",
    "hidapi",
    # Claude Code control server (mcp_microscope_server.py)
    "mcp",
    # Test suite
    "pytest",
    "pytest-qt"
)

Write-Host "Installing Python dependencies..."
& $PythonExe -m pip install --upgrade pip setuptools wheel
Assert-LastExitCode "pip install --upgrade pip"

& $PythonExe -m pip install @packages
Assert-LastExitCode "pip install"

# Catch a mixed-binding environment early: with both bindings present qtpy
# picks PyQt5 or PyQt6 depending on import order, and the GUI fails in ways
# that look nothing like an install problem.
$pyqt6Probe = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyQt6') is None else 1)"
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $PythonExe -c $pyqt6Probe 2>$null
} finally {
    $ErrorActionPreference = $previousPreference
}
if ($LASTEXITCODE -ne 0) {
    Write-Warning ("PyQt6 is installed alongside PyQt5. Squid requires PyQt5; run " +
                   "'`"$PythonExe`" -m pip uninstall PyQt6 PyQt6-Qt6 PyQt6-sip' before launching.")
}

# ---------------------------------------------------------------------------
# Camera drivers
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "CAMERA DRIVER INSTALLATION" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "Please install camera drivers manually:"
Write-Host "  - Daheng Camera: Download Galaxy SDK from https://www.dahengimaging.com/"
Write-Host "  - ToupCam: DLL is included in the repository"
Write-Host ""

# ---------------------------------------------------------------------------
# Desktop shortcut
# ---------------------------------------------------------------------------

Write-Host "Creating desktop shortcut..."
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "Squid_hcs.lnk"
$iconPath = Join-Path $SQUID_SOFTWARE_ROOT "icon\cephla_logo.ico"
$mainScript = Join-Path $SQUID_SOFTWARE_ROOT "main_hcs.py"

$WshShell = New-Object -ComObject WScript.Shell
$shortcut = $WshShell.CreateShortcut($shortcutPath)
# Full interpreter path, not "python": the shortcut must use the same 3.12 we
# just installed into, even if PATH later resolves "python" to something else.
$shortcut.TargetPath = $PythonExe
$shortcut.Arguments = "`"$mainScript`""
$shortcut.WorkingDirectory = $SQUID_SOFTWARE_ROOT
if (Test-Path $iconPath) {
    $shortcut.IconLocation = $iconPath
}
$shortcut.Save()

Write-Host "Desktop shortcut created at: $shortcutPath" -ForegroundColor Green

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Setup complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "You can launch Squid by double-clicking the desktop shortcut."
