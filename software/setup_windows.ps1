<#
.SYNOPSIS
    Set up Squid on Windows 10/11: Git, Python 3.12, the repository, its Python packages and a desktop shortcut.

.DESCRIPTION
    Windows counterpart of setup_22.04.sh / setup_24.04.sh. Keep the pip package lists in sync with those
    scripts (tests/test_setup_scripts.py checks it).

    Packages are installed into Python 3.12 itself, not a virtual environment, so `py -3.12 main_hcs.py` runs
    Squid. It is meant for a fresh computer; any other Python already installed is left untouched.

    Run it from PowerShell (no administrator rights needed; installing Git or Python with winget may ask):
        powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
    Run as administrator to also install the Toupcam USB camera driver.

.PARAMETER RepoPath
    Where the repository is cloned when the script is not run from inside a checkout. Default: Desktop\Squid.
#>
param(
    [string]$RepoPath = (Join-Path ([Environment]::GetFolderPath("Desktop")) "Squid")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$SquidRepoUrl = "https://github.com/Cephla-Lab/Squid.git"
$PythonVersion = "3.12"

function Invoke-Native {
    # Run a program and stop the script if it fails (PowerShell does not do that for programs by itself).
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Quiet {
    # Run a program for its output, ignoring its stderr and exit code; the caller checks $LASTEXITCODE.
    # In Windows PowerShell 5.1 a program's redirected stderr is an error record, which "Stop" would turn into a
    # terminating error (e.g. `py -3.12` printing "No suitable Python runtime found"), so relax it here.
    param([string]$Exe, [string[]]$Arguments)
    $ErrorActionPreference = "Continue"
    & $Exe @Arguments 2>$null
}

function Update-SessionPath {
    # Pick up PATH changes made by an installer in this session.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Install-WithWinget {
    param([string]$Id, [string]$What, [string]$ManualUrl, [string]$InstallerArgs = "")
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$What is not installed and winget is not available. Install it from $ManualUrl and run this script again."
    }
    Write-Host "Installing $What with winget..."
    $wingetArgs = @("install", "--id", $Id, "--exact", "--source", "winget",
        "--accept-package-agreements", "--accept-source-agreements")
    if ($InstallerArgs) { $wingetArgs += @("--override", $InstallerArgs) }
    Invoke-Native winget $wingetArgs
    Update-SessionPath
}

function Find-Git {
    $cmd = Get-Command git -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @("$env:ProgramFiles\Git\cmd\git.exe", "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe")) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Find-Python {
    # The full path of Python 3.12's python.exe, or $null. Tries the py launcher, then the usual install folders.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $exe = Invoke-Quiet py @("-$PythonVersion", "-c", "import sys; print(sys.executable)")
        if ($LASTEXITCODE -eq 0 -and $exe) { return ([string]$exe).Trim() }
    }
    $folder = "Python" + $PythonVersion.Replace(".", "")
    foreach ($candidate in @("$env:LOCALAPPDATA\Programs\Python\$folder\python.exe", "$env:ProgramFiles\$folder\python.exe")) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

# --- Git and Python 3.12 -----------------------------------------------------------------------------------------
$Git = Find-Git
if (-not $Git) {
    Install-WithWinget "Git.Git" "Git" "https://git-scm.com/download/win"
    $Git = Find-Git
    if (-not $Git) { throw "Git was installed but cannot be found. Open a new PowerShell window and run this script again." }
}
Write-Host "Using Git: $Git"

$Python = Find-Python
if (-not $Python) {
    # python.org installer options: just for this user (no administrator rights), with the py launcher also
    # installed just for this user (its default, all users, needs administrator rights and a silent install can
    # skip it), and Python on PATH. The launcher runs the newest installed Python, so on a machine without
    # Python, or with only older ones, plain `py` then runs 3.12.
    Install-WithWinget "Python.Python.$PythonVersion" "Python $PythonVersion" "https://www.python.org/downloads/windows/" `
        "/quiet InstallAllUsers=0 Include_launcher=1 InstallLauncherAllUsers=0 PrependPath=1 Include_test=0"
    $Python = Find-Python
    if (-not $Python) { throw "Python $PythonVersion was installed but cannot be found. Open a new PowerShell window and run this script again." }
}
Write-Host "Using Python: $Python"

function Invoke-Pip {
    Invoke-Native $Python (@("-m", "pip") + $args)
}

# --- The repository ----------------------------------------------------------------------------------------------
# Run from inside a checkout (software\setup_windows.ps1), use that checkout; otherwise clone to -RepoPath.
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "main_hcs.py"))) {
    $RepoPath = Split-Path -Parent $PSScriptRoot
    Write-Host "Using the repository this script is in: $RepoPath"
} elseif (Test-Path (Join-Path $RepoPath "software\main_hcs.py")) {
    Write-Host "Using the existing repository at $RepoPath"
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $RepoPath) | Out-Null
    Invoke-Native $Git @("clone", $SquidRepoUrl, $RepoPath)
}
# The NDViewer tab and fluidics are submodules; a failure only leaves those features off.
& $Git -C $RepoPath submodule update --init --recursive
if ($LASTEXITCODE -ne 0) { Write-Warning "Could not check out the submodules (ndviewer_light, fluidics_v2); continuing." }

$Software = Join-Path $RepoPath "software"
Set-Location $Software
New-Item -ItemType Directory -Force -Path (Join-Path $Software "cache") | Out-Null

# Squid needs machine_configs\illumination_channel_config.yaml, which is gitignored (each machine edits its own).
# Start from the example if it's missing; never overwrite an existing one.
$IlluminationConfig = Join-Path $Software "machine_configs\illumination_channel_config.yaml"
if (-not (Test-Path $IlluminationConfig)) {
    Copy-Item "$IlluminationConfig.example" $IlluminationConfig
    Write-Host "Created $IlluminationConfig from the example; check it matches this machine's hardware."
} else {
    Write-Host "Keeping the existing $IlluminationConfig"
}

# --- Python packages (keep in sync with setup_22.04.sh) -------------------------------------------------------
# Flags are quoted so PowerShell passes them on to pip instead of reading them as Invoke-Pip's own parameters.
Invoke-Pip install "--upgrade" pip

# See setup_22.04.sh for why each pin is there (napari/PyQt6 tested combination, mcp<2, no numpy<2).
Invoke-Pip install pyqtgraph qtpy pyserial pandas imageio crc==1.3.0 lxml numpy tifffile scipy pyreadline3
Invoke-Pip install opencv-python-headless opencv-contrib-python-headless
Invoke-Pip install "napari[pyqt6]==0.7.1" "PyQt6==6.11.0" "PyQt6-Qt6==6.11.2" "PyQt6-sip==13.12.0" scikit-image dask_image ome_zarr tensorstore pytest pytest-qt pytest-xvfb gitpython matplotlib pydantic_xml pyvisa pyvisa-py pyusb hidapi filelock lxml_html_clean psutil "mcp<2" ndv xarray

# Optional: PI V-308 / C-414 focus stage (USE_PI_FOCUS_STAGE).
try { Invoke-Pip install pipython } catch { Write-Warning "pipython install failed; continuing (only needed for USE_PI_FOCUS_STAGE)." }

# Fluidics library (RUN_FLUIDICS), the fluidics_v2 submodule. It declares PyQt5 as a dependency, so install it
# --no-deps to keep pip from adding PyQt5 next to PyQt6; its real runtime deps follow.
Invoke-Pip install cobs platformdirs ruamel.yaml pyyaml "pydantic>=2,<3"
try { Invoke-Pip install "--no-deps" "-e" fluidics_v2/software } catch { Write-Warning "fluidics library install failed; continuing (only needed for RUN_FLUIDICS)." }

# --- Toupcam USB camera driver (needs administrator rights) ----------------------------------------------------
# Squid loads toupcam.dll from the repository; Windows still needs the USB driver to see the camera.
$ToupcamInf = Join-Path $Software "drivers and libraries\toupcam\windows\drivers\usb\x64\toupcam.inf"
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($IsAdmin) {
    & pnputil /add-driver "$ToupcamInf" /install
    if ($LASTEXITCODE -ne 0) { Write-Warning "Installing the Toupcam USB driver failed (exit code $LASTEXITCODE)." }
} else {
    Write-Host "Not running as administrator, so the Toupcam USB driver was not installed. If this microscope has a"
    Write-Host "Toupcam camera, run in an administrator PowerShell:"
    Write-Host "    pnputil /add-driver `"$ToupcamInf`" /install"
}

# --- Desktop shortcut --------------------------------------------------------------------------------------------
# python.exe rather than pythonw.exe: the console window shows Squid's log, as the Linux shortcut's terminal does.
$Shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Squid.lnk"
$Link = (New-Object -ComObject WScript.Shell).CreateShortcut($Shortcut)
$Link.TargetPath = $Python
$Link.Arguments = "`"$(Join-Path $Software 'main_hcs.py')`""
$Link.WorkingDirectory = $Software
$Link.IconLocation = Join-Path $Software "icon\cephla_logo.ico"
$Link.Save()
Write-Host "Desktop shortcut created at: $Shortcut"

# Report which Python a plain `py` runs: it is the newest installed one, unless PY_PYTHON or py.ini says otherwise.
$DefaultPy = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $DefaultPy = Invoke-Quiet py @("-c", "import sys; print(sys.executable)")
    if ($LASTEXITCODE -ne 0 -or -not $DefaultPy) { $DefaultPy = $null } else { $DefaultPy = ([string]$DefaultPy).Trim() }
}
$RunCommand = "py -$PythonVersion"
if ($DefaultPy -and ((Resolve-Path $DefaultPy).Path -eq (Resolve-Path $Python).Path)) {
    $RunCommand = "py"
    Write-Host "py runs Python $PythonVersion ($Python)."
} elseif ($DefaultPy) {
    Write-Warning "py runs $DefaultPy, not Python $PythonVersion (a newer Python, or PY_PYTHON / py.ini). Start Squid with: py -$PythonVersion main_hcs.py"
} else {
    Write-Warning "The py launcher is not available; start Squid with: `"$Python`" main_hcs.py"
    $RunCommand = "`"$Python`""
}

Write-Host ""
Write-Host "Done. Before the first start:"
Write-Host "  1. Copy this microscope's configuration_*.ini from $Software\configurations into $Software."
Write-Host "  2. Install the drivers for this microscope's cameras (see README.md), then restart the computer."
Write-Host "To try Squid without hardware: $RunCommand main_hcs.py --simulation (from $Software)"
