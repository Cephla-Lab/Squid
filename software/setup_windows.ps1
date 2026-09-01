#Requires -Version 5.1
<#
.SYNOPSIS
    Setup script for Squid on Windows.
.DESCRIPTION
    Installs Python 3.12 and Git if they are missing, clones the repository if
    needed, installs Python dependencies, and creates a desktop shortcut.

    Both prerequisites are fetched from their official download hosts, checked
    against a pinned SHA256 before being run, and installed silently per-user
    so no admin rights or UAC prompts are involved. -SkipPythonInstall and
    -SkipGitInstall opt out of either.

    This script targets Python 3.12 and napari 0.7:

      1. Python 3.12 is required exactly. napari 0.7 supports 3.10-3.14, but
         3.12 is the newest version with wheels for every dependency here
         (notably PyQt5 and hidapi), and pinning one interpreter keeps every
         Windows install reproducible.

         If no 3.12 is present the script downloads the official installer
         from python.org and runs it silently, per-user, so no admin rights
         or UAC prompt are needed. It installs 3.12.10 specifically: that is
         the final 3.12 release with a Windows binary installer (3.12.11 and
         later are source-only security releases). Pass -SkipPythonInstall to
         require a pre-existing interpreter instead.

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
.PARAMETER SkipPythonInstall
    Fail instead of installing Python 3.12 when no suitable interpreter is
    found. Useful on machines where Python is managed centrally.
.PARAMETER SkipGitInstall
    Fail instead of installing Git when it is not found.
#>

param(
    [string]$RepoPath = "$env:USERPROFILE\Desktop\Squid",
    [switch]$SkipPythonInstall,
    [switch]$SkipGitInstall
)

$ErrorActionPreference = "Stop"

# Required Python version. napari 0.7 also runs on 3.10/3.11/3.13/3.14, but we
# pin one version so every Windows machine ends up with the same environment.
$REQUIRED_PYTHON_MAJOR = 3
$REQUIRED_PYTHON_MINOR = 12
$REQUIRED_PYTHON = "$REQUIRED_PYTHON_MAJOR.$REQUIRED_PYTHON_MINOR"

# 3.12.10 is the last 3.12 with a Windows binary installer; 3.12.11+ are
# source-only security releases, so this version does not float.
#
# The hash is of https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe.
# To re-derive it after a version bump:
#   (Get-FileHash .\python-3.12.10-amd64.exe -Algorithm SHA256).Hash
$PYTHON_INSTALLER_VERSION = "3.12.10"
$PYTHON_INSTALLER_SHA256 = "67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB"
$PYTHON_INSTALLER_URL =
    "https://www.python.org/ftp/python/$PYTHON_INSTALLER_VERSION/python-$PYTHON_INSTALLER_VERSION-amd64.exe"

# Git is pinned too, for reproducibility rather than compatibility - Squid
# works with any modern Git. To bump: pick a release from
# https://github.com/git-for-windows/git/releases and take the SHA256 that
# the release notes publish next to the .exe.
$GIT_INSTALLER_VERSION = "2.55.0.3"
$GIT_INSTALLER_TAG = "v2.55.0.windows.3"
$GIT_INSTALLER_SHA256 = "AF12577D0FDFF74243A5988197AA49B957D5044EDC17004F6DDF0768996F1DCA"
$GIT_INSTALLER_URL =
    "https://github.com/git-for-windows/git/releases/download/$GIT_INSTALLER_TAG/Git-$GIT_INSTALLER_VERSION-64-bit.exe"

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
             "print(64 if sys.maxsize > 2**32 else 32); print(sys.executable)"

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

# Locate a 64-bit Python 3.12, or return $null. Interpreters that were found
# but rejected are recorded in $script:FoundPythonVersions for the error path.
function Find-Python312 {
    # The py launcher is tried first because it can select an exact version
    # even when a different Python owns the PATH.
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += @{ Exe = "py"; Prefix = @("-$REQUIRED_PYTHON") }
    }
    foreach ($name in @("python$REQUIRED_PYTHON", "python3", "python")) {
        if (Get-Command $name -ErrorAction SilentlyContinue) {
            $candidates += @{ Exe = $name; Prefix = @() }
        }
    }
    # Default per-user install location, checked explicitly because a Python
    # this script just installed is not on this process's PATH.
    $defaultInstall = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $defaultInstall) {
        $candidates += @{ Exe = $defaultInstall; Prefix = @() }
    }

    $script:FoundPythonVersions = @()
    foreach ($candidate in $candidates) {
        $info = Get-PythonInfo -Exe $candidate.Exe -Prefix $candidate.Prefix
        if ($null -eq $info) {
            continue
        }

        $version = "$($info.Major).$($info.Minor)"
        $script:FoundPythonVersions += "$version ($($info.Executable))"

        if ($info.Major -ne $REQUIRED_PYTHON_MAJOR -or $info.Minor -ne $REQUIRED_PYTHON_MINOR) {
            continue
        }
        if ($info.Bits -ne 64) {
            Write-Warning "Ignoring 32-bit Python at $($info.Executable); Squid needs 64-bit Python."
            continue
        }

        # Return sys.executable rather than the launcher so every later call
        # (pip, the desktop shortcut) is pinned to this exact interpreter.
        return $info.Executable
    }

    return $null
}

# Locate git.exe, or return $null.
function Find-Git {
    $command = Get-Command git -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    # Not on PATH. A Git this script just installed will not be either, since
    # the installer only edits PATH for new processes - but it does record
    # where it landed: HKLM for an elevated install, HKCU for a per-user one.
    foreach ($key in @("HKCU:\Software\GitForWindows", "HKLM:\Software\GitForWindows")) {
        $installPath = (Get-ItemProperty -Path $key -Name InstallPath -ErrorAction SilentlyContinue).InstallPath
        if ($installPath) {
            $exe = Join-Path $installPath "cmd\git.exe"
            if (Test-Path $exe) {
                return $exe
            }
        }
    }

    foreach ($dir in @("$env:LOCALAPPDATA\Programs\Git", "$env:ProgramFiles\Git")) {
        $exe = Join-Path $dir "cmd\git.exe"
        if (Test-Path $exe) {
            return $exe
        }
    }

    return $null
}

# Download an installer, verify it against a known SHA256, and run it silently.
function Invoke-SilentInstaller {
    param(
        [string]$DisplayName,
        [string]$Url,
        [string]$Sha256,
        [string]$FileName,
        [string[]]$InstallerArgs,
        # Inno Setup and the Python installer both use 3010 for "succeeded,
        # reboot pending" - the payload is usable immediately either way.
        [int[]]$SuccessExitCodes = @(0, 3010),
        [string]$ManualUrl
    )

    $installer = Join-Path $env:TEMP $FileName

    Write-Host "Downloading $DisplayName from $Url"
    # Invoke-WebRequest renders a progress bar per chunk in Windows PowerShell,
    # which turns a 30-70 MB download into a multi-minute one. Older Windows
    # also defaults to TLS 1.0, which both download hosts refuse.
    $previousProgress = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $Url -OutFile $installer -UseBasicParsing
    } finally {
        $ProgressPreference = $previousProgress
    }

    $actualHash = (Get-FileHash -Path $installer -Algorithm SHA256).Hash
    if ($actualHash -ne $Sha256) {
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
        throw ("Downloaded $DisplayName installer failed its integrity check; not running it.`n" +
               "  expected SHA256: $Sha256`n" +
               "  actual   SHA256: $actualHash")
    }

    Write-Host "Installing $DisplayName (per-user, no admin rights needed)..."
    $process = Start-Process -FilePath $installer -ArgumentList $InstallerArgs -Wait -PassThru
    Remove-Item $installer -Force -ErrorAction SilentlyContinue

    if ($SuccessExitCodes -notcontains $process.ExitCode) {
        throw ("$DisplayName installer failed with exit code $($process.ExitCode). " +
               "Install it manually from $ManualUrl and re-run this script.")
    }

    # The installers edit PATH for *new* processes; refresh this one so the
    # re-probe by the caller can see what was just installed.
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machinePath, $userPath) | Where-Object { $_ }) -join ";"
}

function Install-Python312 {
    # InstallAllUsers=0 and InstallLauncherAllUsers=0 keep this out of Program
    # Files, so the install runs without a UAC prompt. Both PrependPath and the
    # py launcher are wanted: PrependPath for interactive use, the launcher so
    # `py -3.12` finds this interpreter regardless of PATH order.
    Invoke-SilentInstaller `
        -DisplayName "Python $PYTHON_INSTALLER_VERSION" `
        -Url $PYTHON_INSTALLER_URL `
        -Sha256 $PYTHON_INSTALLER_SHA256 `
        -FileName "python-$PYTHON_INSTALLER_VERSION-amd64.exe" `
        -ManualUrl "https://www.python.org/downloads/" `
        -InstallerArgs @(
            "/quiet",
            "InstallAllUsers=0",
            "PrependPath=1",
            "Include_launcher=1",
            "InstallLauncherAllUsers=0",
            "Include_test=0",
            "AssociateFiles=0",
            "Shortcuts=0"
        )
}

function Install-Git {
    # Git for Windows is an Inno Setup installer built with
    # PrivilegesRequired=none, so an unelevated run installs per-user into
    # %LOCALAPPDATA%\Programs\Git with no UAC prompt, and records itself under
    # HKCU instead of HKLM. Run elevated it installs machine-wide instead;
    # both are fine, and Find-Git handles either.
    #
    # The default PathOption already puts git on PATH, which is what the clone
    # below and everyday use both need.
    Invoke-SilentInstaller `
        -DisplayName "Git $GIT_INSTALLER_VERSION" `
        -Url $GIT_INSTALLER_URL `
        -Sha256 $GIT_INSTALLER_SHA256 `
        -FileName "Git-$GIT_INSTALLER_VERSION-64-bit.exe" `
        -ManualUrl "https://git-scm.com/download/win" `
        -InstallerArgs @(
            "/VERYSILENT",
            "/NORESTART",
            "/SP-",
            "/SUPPRESSMSGBOXES",
            "/NOCANCEL"
        )
}

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

$PythonExe = Find-Python312

if ($null -eq $PythonExe) {
    $detail = if ($script:FoundPythonVersions.Count -gt 0) {
        "Found instead: " + ($script:FoundPythonVersions -join ", ") + "."
    } else {
        "No Python interpreter was found on PATH."
    }

    if ($SkipPythonInstall) {
        Write-Error ("64-bit Python $REQUIRED_PYTHON is required but was not found. $detail`n" +
                     "Install it from https://www.python.org/downloads/ (check 'Add python.exe " +
                     "to PATH'), or re-run without -SkipPythonInstall to install it automatically.")
        exit 1
    }

    Write-Host "64-bit Python $REQUIRED_PYTHON not found. $detail"
    Install-Python312

    $PythonExe = Find-Python312
    if ($null -eq $PythonExe) {
        throw ("Python $REQUIRED_PYTHON still could not be found after installing it. " +
               "Try opening a new terminal and re-running this script.")
    }
}
Write-Host "Using Python $REQUIRED_PYTHON at $PythonExe"

$GitExe = Find-Git

if ($null -eq $GitExe) {
    if ($SkipGitInstall) {
        Write-Error ("Git was not found. Install it from https://git-scm.com/download/win, " +
                     "or re-run without -SkipGitInstall to install it automatically.")
        exit 1
    }

    Write-Host "Git not found."
    Install-Git

    $GitExe = Find-Git
    if ($null -eq $GitExe) {
        throw ("Git still could not be found after installing it. " +
               "Try opening a new terminal and re-running this script.")
    }
}
Write-Host "Using Git at $GitExe"
Write-Host "Found $(& $GitExe --version)"

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
    & $GitExe clone --recurse-submodules $SQUID_REPO_HTTP $RepoPath
    Assert-LastExitCode "git clone"
} else {
    $currentHead = & $GitExe -C $RepoPath rev-parse HEAD
    Assert-LastExitCode "git rev-parse"
    Write-Host "Using existing repo at '$RepoPath' at HEAD=$currentHead"
    Write-Host "Updating submodules..."
    & $GitExe -C $RepoPath submodule update --init --recursive
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
