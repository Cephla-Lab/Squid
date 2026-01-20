#Requires -Version 5.1
<#
.SYNOPSIS
    Setup script for Squid on Windows.
.DESCRIPTION
    Installs Python dependencies, clones the repository if needed, and creates a desktop shortcut.
.PARAMETER RepoPath
    Path where Squid repository should be cloned. Defaults to Desktop\Squid.
#>

param(
    [string]$RepoPath = "$env:USERPROFILE\Desktop\Squid"
)

$ErrorActionPreference = "Stop"

Write-Host "Using SQUID_REPO_PATH='$RepoPath'"

$SQUID_REPO_HTTP = "https://github.com/Cephla-Lab/Squid.git"
$SQUID_SOFTWARE_ROOT = Join-Path $RepoPath "software"
$SQUID_REPO_PATH_PARENT = Split-Path $RepoPath -Parent

# Check if Python is installed
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Found $pythonVersion"
} catch {
    Write-Error "Python is not installed or not in PATH. Please install Python 3.10+ from https://python.org"
    exit 1
}

# Check if git is installed
try {
    $gitVersion = git --version 2>&1
    Write-Host "Found $gitVersion"
} catch {
    Write-Error "Git is not installed or not in PATH. Please install Git from https://git-scm.com"
    exit 1
}

# Clone the repo if we don't already have it
if (-not (Test-Path $SQUID_REPO_PATH_PARENT)) {
    New-Item -ItemType Directory -Path $SQUID_REPO_PATH_PARENT -Force | Out-Null
}

if (-not (Test-Path $RepoPath)) {
    Write-Host "Cloning Squid repository..."
    git clone $SQUID_REPO_HTTP $RepoPath
} else {
    $currentHead = git -C $RepoPath rev-parse HEAD
    Write-Host "Using existing repo at '$RepoPath' at HEAD=$currentHead"
}

# Create cache directory
$cacheDir = Join-Path $SQUID_SOFTWARE_ROOT "cache"
if (-not (Test-Path $cacheDir)) {
    New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
}

# Install Python libraries
Write-Host "Installing Python dependencies..."
python -m pip install --upgrade pip

python -m pip install qtpy pyserial pandas imageio "crc==1.3.0" lxml numpy tifffile scipy napari pyreadline3
python -m pip install opencv-python-headless opencv-contrib-python-headless
python -m pip install "napari[all]" scikit-image dask_image ome_zarr aicsimageio basicpy pytest pytest-qt gitpython matplotlib pydantic_xml pyvisa hidapi psutil

# Camera driver notes
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "CAMERA DRIVER INSTALLATION" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "Please install camera drivers manually:"
Write-Host "  - Daheng Camera: Download Galaxy SDK from https://www.dahengimaging.com/"
Write-Host "  - ToupCam: DLL is included in the repository"
Write-Host ""

# Create desktop shortcut
Write-Host "Creating desktop shortcut..."
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "Squid_hcs.lnk"
$iconPath = Join-Path $SQUID_SOFTWARE_ROOT "icon\cephla_logo.ico"
$mainScript = Join-Path $SQUID_SOFTWARE_ROOT "main_hcs.py"

$WshShell = New-Object -ComObject WScript.Shell
$shortcut = $WshShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "python"
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
