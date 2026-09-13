<#
.SYNOPSIS
    Set up the Squid HCS software on Windows 10/11 (x64).

.DESCRIPTION
    The Windows counterpart to setup_22.04.sh. Installs Git and Python, clones the
    repo, builds a virtualenv with the pinned PyQt6 + napari stack, seeds the
    machine configuration, and creates Desktop shortcuts.

    Differences from the Ubuntu script, all forced by the platform:

      * A virtualenv is used instead of a system-wide pip install. Windows has no
        distro Python to keep clean, and an isolated env is the only way to
        guarantee exactly one Qt binding is present.
      * apt / udev / dialout / apt-mark steps have no Windows equivalent and are
        dropped. Serial ports need no group membership here.
      * Camera drivers are vendor installers rather than bundled .run files.
        ToupTek needs nothing (see below); Daheng needs the Galaxy SDK.

.PARAMETER RepoPath
    Where the repo lives (or will be cloned). Defaults to <Desktop>\Squid.

.PARAMETER SkipPrereqs
    Skip installing Git/Python via winget. Use when they are already present or
    managed some other way.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup_windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup_windows.ps1 -RepoPath D:\Squid
#>

[CmdletBinding()]
param(
    [string] $RepoPath = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Squid'),
    [switch] $SkipPrereqs
)

# Equivalent of `set -eo pipefail`: stop on the first error rather than limping on
# with a half-built environment.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # keeps winget/pip progress bars out of logs

$RepoHttp = 'https://github.com/Cephla-Lab/Squid.git'
$PythonVersionId = 'Python.Python.3.12'  # 3.12 is the tested interpreter

function Write-Step { param([string] $Message) Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Write-Note { param([string] $Message) Write-Host "    $Message" -ForegroundColor DarkGray }
function Write-Warn { param([string] $Message) Write-Host "    WARNING: $Message" -ForegroundColor Yellow }

function Update-PathFromRegistry {
    # winget writes the new PATH to the registry, but the running process keeps the
    # environment it started with. Without this refresh, git.exe and py.exe installed
    # a moment ago are still not callable from this script.
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('PATH', 'User')
}

function Invoke-Pip {
    # pip failures must be fatal: a partially-resolved environment is worse than none,
    # and PowerShell does not treat a nonzero native exit code as a terminating error.
    param([string] $VenvPython, [string[]] $PipArgs, [string] $What)
    Write-Note "pip install $What"
    & $VenvPython -m pip install --disable-pip-version-check @PipArgs
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed ($What). Exit code $LASTEXITCODE."
    }
}

# ---------------------------------------------------------------------------
# 1. Prerequisites: Git and Python
# ---------------------------------------------------------------------------
if (-not $SkipPrereqs) {
    Write-Step 'Installing prerequisites (Git, Python 3.12)'

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'winget not found. Install "App Installer" from the Microsoft Store, or re-run with -SkipPrereqs after installing Git and Python 3.12 manually.'
    }

    foreach ($pkg in @('Git.Git', $PythonVersionId)) {
        Write-Note "winget install $pkg"
        winget install --id $pkg --exact --source winget --silent `
            --accept-source-agreements --accept-package-agreements --disable-interactivity
        # winget exits nonzero when a package is already installed; that is not an error.
    }
    Update-PathFromRegistry
}
else {
    Update-PathFromRegistry
}

# control/utils.py imports GitPython at module scope, and GitPython raises ImportError
# at import time if it cannot find a git executable. So git on PATH is a hard runtime
# requirement, not just a requirement of this installer.
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is not on PATH. Squid imports GitPython at startup, which fails without it. Open a new terminal (so PATH is refreshed) and re-run.'
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $PyLauncher) {
    throw 'The py launcher was not found. Install Python 3.12 from python.org (with "Add to PATH"), then re-run with -SkipPrereqs.'
}

# ---------------------------------------------------------------------------
# 2. The repo, including submodules
# ---------------------------------------------------------------------------
Write-Step "Fetching the repo into '$RepoPath'"

if (-not (Test-Path -LiteralPath $RepoPath)) {
    $parent = Split-Path -Parent $RepoPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    # --recursive matters: control/ndviewer_light and fluidics_v2 are submodules, and a
    # plain clone leaves them as empty directories.
    git clone --recursive $RepoHttp $RepoPath
}
else {
    Push-Location $RepoPath
    Write-Note "Using existing repo at HEAD=$(git rev-parse HEAD)"
    # Brings submodules up to the commits this checkout records, and fills them in if an
    # earlier clone omitted --recursive. Note this discards uncommitted submodule work,
    # checking each one out at the recorded SHA in detached HEAD.
    git submodule update --init --recursive
    Pop-Location
}

$SoftwareRoot = Join-Path $RepoPath 'software'
if (-not (Test-Path -LiteralPath $SoftwareRoot)) {
    throw "Expected '$SoftwareRoot' to exist. Is '$RepoPath' really a Squid checkout?"
}

Push-Location $SoftwareRoot
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $SoftwareRoot 'cache') | Out-Null

    # -----------------------------------------------------------------------
    # 3. Virtualenv
    # -----------------------------------------------------------------------
    Write-Step 'Creating the virtualenv'
    $VenvPython = Join-Path $SoftwareRoot '.venv\Scripts\python.exe'

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        & py -3.12 -m venv (Join-Path $SoftwareRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtualenv. Exit code $LASTEXITCODE." }
    }
    else {
        Write-Note 'Reusing the existing .venv'
    }

    Invoke-Pip $VenvPython @('--upgrade', 'pip', 'setuptools', 'wheel') 'pip, setuptools, wheel'

    # -----------------------------------------------------------------------
    # 4. Qt binding
    # -----------------------------------------------------------------------
    # Exactly ONE Qt binding may be present: PyQt5 and PyQt6 together break
    # napari/vispy OpenGL rendering (blank canvases, "Cannot SIZE object N because it
    # does not exist"). The venv gives us that guarantee for free.
    #
    # Pinned to the tested combination. napari 0.7.1 blocklists PyQt6-Qt6 6.11.0 and
    # 6.11.1 (dock-widget and resizing bugs, napari/napari#9052), which is why Qt is
    # 6.11.2; the napari[pyqt6] extra below makes pip re-check these pins, so bump
    # them together.
    Write-Step 'Installing the Qt binding (PyQt6)'
    Invoke-Pip $VenvPython @('PyQt6==6.11.0', 'PyQt6-Qt6==6.11.2', 'PyQt6-sip==13.12.0') 'PyQt6 6.11.0 / Qt 6.11.2'

    # -----------------------------------------------------------------------
    # 5. Python dependencies
    # -----------------------------------------------------------------------
    Write-Step 'Installing Python dependencies'

    Invoke-Pip $VenvPython @(
        'pyqtgraph', 'qtpy', 'pyserial', 'pandas', 'imageio', 'crc==1.3.0',
        'lxml', 'numpy', 'tifffile', 'scipy', 'pyreadline3'
    ) 'base libraries'

    Invoke-Pip $VenvPython @('opencv-python-headless', 'opencv-contrib-python-headless') 'OpenCV (headless)'

    # napari is pinned to a tested release because patch releases move its vispy/Qt
    # constraints. tensorstore is required by control/ndviewer_light and by
    # tests/control/core/test_zarr_writer.py, which opens with
    # pytest.importorskip("tensorstore").
    #
    # aicsimageio and basicpy are intentionally absent. basicpy pins scipy<1.13 and
    # scipy 1.12 has no NumPy-2 wheel, so including it drags the environment back to
    # numpy 1.26 and breaks napari's scipy>=1.14 and opencv 5.x's numpy>=2. Their only
    # consumer is control/stitcher.py, which nothing in the application imports;
    # stitching lives in the separate Cephla-Lab/image-stitcher repo. Install them
    # into a separate venv if you need that module.
    Invoke-Pip $VenvPython @(
        'napari[pyqt6]==0.7.1', 'scikit-image', 'dask_image', 'ome_zarr', 'tensorstore',
        'pytest', 'pytest-qt', 'gitpython', 'matplotlib', 'pydantic_xml', 'pyvisa',
        'hidapi', 'filelock', 'lxml_html_clean', 'psutil', 'mcp', 'ndv'
    ) 'napari 0.7.1 and the rest of the stack'

    # Optional: PI V-308 / C-414 focus stage (USE_PI_FOCUS_STAGE). squid.stage.pi
    # imports it lazily and only needs it to talk to real hardware, so a failure here
    # must not abort the install.
    Write-Note 'pip install pipython (optional)'
    & $VenvPython -m pip install --disable-pip-version-check pipython
    if ($LASTEXITCODE -ne 0) {
        Write-Warn 'pipython install failed; continuing (only needed for USE_PI_FOCUS_STAGE).'
    }

    Write-Step 'Verifying the environment'
    & $VenvPython -m pip check
    if ($LASTEXITCODE -ne 0) { Write-Warn 'pip check reported broken requirements (see above).' }

    # Kept to a single line: a multi-line script handed to `python -c` gets re-parsed by
    # PowerShell's native-argument handling and arrives at the interpreter mangled.
    & $VenvPython -c "import qtpy, napari, numpy, cv2; from qtpy import QtCore; print('  qtpy binding :', qtpy.API_NAME); print('  Qt           :', QtCore.__version__); print('  napari       :', napari.__version__); print('  numpy        :', numpy.__version__); print('  opencv       :', cv2.__version__)"
    if ($LASTEXITCODE -ne 0) { throw 'The installed stack failed to import. See the traceback above.' }

    # -----------------------------------------------------------------------
    # 6. Machine configuration
    # -----------------------------------------------------------------------
    Write-Step 'Seeding machine configuration'

    $IlluminationExample = Join-Path $SoftwareRoot 'machine_configs\illumination_channel_config.yaml.example'
    $IlluminationConfig = Join-Path $SoftwareRoot 'machine_configs\illumination_channel_config.yaml'
    if ((Test-Path -LiteralPath $IlluminationExample) -and -not (Test-Path -LiteralPath $IlluminationConfig)) {
        Copy-Item -LiteralPath $IlluminationExample -Destination $IlluminationConfig
        Write-Note 'Created machine_configs\illumination_channel_config.yaml from the example.'
        Write-Warn 'EDIT IT: the example assumes 405/488/561/638/730 laser lines and a stock LED matrix. The GUI degrades and channels are missing if it does not match your engine.'
    }
    else {
        Write-Note 'illumination_channel_config.yaml already present; leaving it alone.'
    }

    # _def.py globs "configuration*.ini" in the software root and silently takes the
    # first match, so more than one is a real hazard. Which one is right depends on the
    # instrument, so this is left to the operator rather than guessed.
    $ConfigsInRoot = @(Get-ChildItem -LiteralPath $SoftwareRoot -Filter 'configuration*.ini' -File -ErrorAction SilentlyContinue)
    if ($ConfigsInRoot.Count -eq 0) {
        Write-Warn 'No configuration*.ini in the software root. Squid needs exactly one. Copy the file matching your instrument:'
        Get-ChildItem -LiteralPath (Join-Path $SoftwareRoot 'configurations') -Filter '*.ini' -File |
            ForEach-Object { Write-Host "      configurations\$($_.Name)" -ForegroundColor DarkGray }
    }
    elseif ($ConfigsInRoot.Count -gt 1) {
        Write-Warn "Found $($ConfigsInRoot.Count) configuration*.ini files in the software root. Squid takes whichever the glob returns first - delete all but the one you want:"
        $ConfigsInRoot | ForEach-Object { Write-Host "      $($_.Name)" -ForegroundColor DarkGray }
    }
    else {
        Write-Note "Using configuration: $($ConfigsInRoot[0].Name)"
    }

    # -----------------------------------------------------------------------
    # 7. Desktop shortcuts
    # -----------------------------------------------------------------------
    Write-Step 'Creating Desktop shortcuts'

    $Launcher = Join-Path $SoftwareRoot 'main_hcs.bat'
    $IconPath = Join-Path $SoftwareRoot 'icon\cephla_logo.ico'
    $Desktop = [Environment]::GetFolderPath('Desktop')
    $WScript = New-Object -ComObject WScript.Shell

    foreach ($sc in @(
            @{ Name = 'Squid'; Args = ''; Desc = 'Squid HCS - real hardware' },
            @{ Name = 'Squid (simulation)'; Args = '--simulation'; Desc = 'Squid HCS - simulated hardware' }
        )) {
        $lnk = $WScript.CreateShortcut((Join-Path $Desktop "$($sc.Name).lnk"))
        $lnk.TargetPath = $Launcher
        $lnk.Arguments = $sc.Args
        $lnk.WorkingDirectory = $SoftwareRoot
        $lnk.Description = $sc.Desc
        if (Test-Path -LiteralPath $IconPath) { $lnk.IconLocation = $IconPath }
        $lnk.Save()
        Write-Note "$Desktop\$($sc.Name).lnk"
    }
}
finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# 8. What is left for the operator
# ---------------------------------------------------------------------------
Write-Step 'Setup complete. Remaining manual steps:'

Write-Host @"

  Camera drivers
    ToupTek   nothing to install - the x64 toupcam.dll ships in the repo under
              'drivers and libraries\toupcam\windows\x64' and control/toupcam.py
              loads it from there directly.
    Daheng    install the Galaxy SDK for Windows (provides GxIAPI.dll, which
              control/gxipy loads by name), then REBOOT. Required for the laser
              autofocus camera. The installer is linked from the repo README.
    Others    Hamamatsu (DCAM-API), Photometrics (PVCAM + pyvcam), Tucsen
              (TUCam.dll under software\lib\x64), FLIR (Spinnaker + PySpin),
              iDS (ids_peak), Andor (pyAndorSDK3) each need their vendor SDK.

  Configuration
    Confirm exactly one configuration*.ini sits in '$SoftwareRoot'
    and that machine_configs\illumination_channel_config.yaml matches your
    laser engine.

  Launching
    Use the Desktop shortcuts, or from '$SoftwareRoot':
      main_hcs.bat              # real hardware
      main_hcs.bat --simulation # no hardware required

"@ -ForegroundColor Gray
