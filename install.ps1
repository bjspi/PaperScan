<#
.SYNOPSIS
    Sets up PaperScan (Windows).

.DESCRIPTION
    Run from a clone, it sets up that folder. Run directly from the web, it clones the
    repository into ~\PaperScan first (and reuses that clone when run again):

        irm https://raw.githubusercontent.com/bjspi/PaperScan/main/install.ps1 | iex

    Creates a virtual environment in .venv (Python 3.12-3.13), installs PaperScan with
    all extras into it, adds a Start menu entry and the paperscan command, then offers
    to install the external tools (Tesseract with languages, cjpegli, adb). Safe to run again.

    Uses uv if it is installed (uv also downloads Python 3.13 if needed), otherwise a Python
    found through the py launcher or on PATH. If there is none, it asks for the path of one.

.PARAMETER NoStart
    Do not start PaperScan after installing.

.PARAMETER SkipTools
    Do not offer to install Tesseract, cjpegli and adb (run "paperscan setup" later).

.PARAMETER Python
    The Python to create the virtual environment with: python.exe or its folder.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -SkipTools -NoStart
#>
[CmdletBinding()]
param(
    [switch]$NoStart,
    [switch]$SkipTools,
    [string]$Python
)

# The script block keeps settings and helpers out of the user's session when run through iex.
& {
    param([bool]$NoStart, [bool]$SkipTools, [string]$ScriptFolder, [string]$CustomPython)

    $ErrorActionPreference = 'Stop'
    $appName = 'PaperScan'
    $repository = 'https://github.com/bjspi/PaperScan.git'
    $supportedVersions = '3.13', '3.12'
    $uvInstalled = [bool](Get-Command uv -ErrorAction SilentlyContinue)
    # Folder for the paperscan command shim; added to the user PATH if missing.
    $commandFolder = Join-Path $HOME '.local\bin'
    # Seconds to wait for a running PaperScan to close (the user may have to answer its question first).
    $closeTimeoutSeconds = 120
    # Seconds the window stays open after a successful run, so the summary can be read.
    $closeDelaySeconds = 10


    function Wait-BeforeClose {
        # Count down before the window closes; any key closes at once. Skipped without a console window.
        param([int]$Seconds)
        if (-not [Environment]::UserInteractive -or [Console]::IsOutputRedirected -or [Console]::IsInputRedirected) { return }
        for ($left = $Seconds; $left -gt 0; $left--) {
            Write-Host -NoNewline "`rThis window closes in $left s (press any key to close now) "
            for ($tick = 0; $tick -lt 10; $tick++) {
                if ([Console]::KeyAvailable) {
                    [Console]::ReadKey($true) | Out-Null
                    Write-Host ''
                    return
                }
                Start-Sleep -Milliseconds 100
            }
        }
        Write-Host ''
    }


    function Get-ProjectFolder {
        # This script's folder in a clone or unpacked release; otherwise ~\PaperScan, cloned if needed.
        if ($ScriptFolder -and (Test-Path (Join-Path $ScriptFolder 'pyproject.toml'))) { return $ScriptFolder }
        $target = Join-Path $HOME $appName
        if (Test-Path (Join-Path $target '.git')) {
            Write-Host "==> Using the existing clone in $target"
            return $target
        }
        if (Test-Path $target) { throw "$target exists but is not a clone of $appName. Move it away and run this again." }
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            throw "Git is required: winget install Git.Git`n(Open a new terminal afterwards and run this again.)"
        }
        Write-Host "==> Cloning $appName into $target"
        git clone --quiet $repository $target | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Cloning the repository failed.' }
        return $target
    }


    function Get-PythonVersion {
        # "3.13" for a working interpreter; nothing for a missing one or the Microsoft Store stub.
        param([string]$Program, [string[]]$Arguments = @())
        $ErrorActionPreference = 'Continue'
        try {
            $version = & $Program @Arguments -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0) { return "$version".Trim() }
        }
        catch {
            return
        }
    }


    function Get-PythonProblem {
        # Why this Python cannot create the environment; nothing when it can.
        param([string]$Program, [string[]]$Arguments = @())
        if ($Program -like '*\Microsoft\WindowsApps\*') { return 'is the Windows Store alias, not an installed Python.' }
        $version = Get-PythonVersion $Program $Arguments
        if (-not $version) { return 'does not run.' }
        if ($supportedVersions -notcontains $version) { return "is Python $version, but $appName needs 3.12-3.13." }
        $ErrorActionPreference = 'Continue'
        & $Program @Arguments -c 'import venv, ensurepip' 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { return 'has no venv or ensurepip module, so it cannot create the environment.' }
    }


    function New-Venv {
        param([string]$Venv, [string]$Program, [string[]]$Arguments = @())
        & $Program @Arguments -m venv $Venv | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Creating the virtual environment failed.' }
    }


    function New-VenvWithFoundPython {
        # Create the venv with the first usable Python: py launcher (newest first), then PATH; False if none.
        param([string]$Venv)
        $candidates = @($supportedVersions | ForEach-Object { , @('py', "-$_") })
        foreach ($command in Get-Command python, python3 -All -CommandType Application -ErrorAction SilentlyContinue) {
            $candidates += , @($command.Source)
        }
        foreach ($candidate in $candidates) {
            $program = $candidate[0]
            $arguments = @($candidate | Select-Object -Skip 1)
            if (Get-PythonProblem $program $arguments) { continue }
            New-Venv $Venv $program $arguments
            return $true
        }
        return $false
    }


    function Resolve-PythonPath {
        # The python.exe that a given path means (quotes, variables and its folder are fine); explains if unusable.
        param([string]$Text)
        $path = [Environment]::ExpandEnvironmentVariables($Text.Trim().Trim('"', "'"))
        if (Test-Path -LiteralPath $path -PathType Container) { $path = Join-Path $path 'python.exe' }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Split-Path -Leaf $path) -notlike 'python*.exe') {
            Write-Host "No python.exe found at $path" -ForegroundColor Yellow
            return
        }
        $problem = Get-PythonProblem $path
        if ($problem) {
            Write-Host "$path $problem" -ForegroundColor Yellow
            return
        }
        return $path
    }


    function Read-PythonPath {
        # Ask until the path is usable; nothing when the user leaves it empty or nobody can answer.
        Write-Host 'Python 3.12-3.13 was not found on this computer.' -ForegroundColor Yellow
        Write-Host 'If you have one somewhere else, enter its path (python.exe or its folder). Leave it empty to cancel.'
        while ($true) {
            try {
                $answer = Read-Host 'Path to Python'
            }
            catch {
                return
            }
            if (-not $answer -or -not $answer.Trim()) { return }
            $path = Resolve-PythonPath $answer
            if ($path) { return $path }
        }
    }


    function Get-UvPython {
        # Path of a Python 3.13 managed by uv (downloaded if needed); nothing if uv cannot provide one.
        $ErrorActionPreference = 'Continue'
        $path = uv python find --system 3.13 2>$null
        if ($LASTEXITCODE -ne 0) {
            uv python install 3.13 2>&1 | Out-Host
            $path = uv python find --system 3.13 2>$null
        }
        if ($LASTEXITCODE -eq 0 -and $path) { return "$path".Trim() }
    }


    function New-Environment {
        # Create .venv unless a usable one exists; returns whether uv provided the Python (uv then installs packages too).
        param([string]$Venv, [string]$VenvPython)
        if (Test-Path $VenvPython) {
            $version = Get-PythonVersion $VenvPython
            if ($supportedVersions -notcontains $version) {
                throw "The existing .venv uses Python $version; $appName needs 3.12-3.13.`nDelete $Venv and run this again."
            }
            Write-Host "==> Using the existing virtual environment (Python $version)"
            return $false
        }
        if ($CustomPython) {
            $path = Resolve-PythonPath $CustomPython
            if (-not $path) { throw 'The Python given with -Python cannot be used.' }
            Write-Host "==> Creating the virtual environment with $path"
            New-Venv $Venv $path
            return $false
        }
        if ($uvInstalled) {
            # uv only provides the interpreter: "uv venv" gives pythonw.exe the console launcher,
            # which opens a black window next to the GUI. Python's own venv module copies the right one.
            $uvPython = Get-UvPython
            if ($uvPython) {
                Write-Host "==> Creating the virtual environment with $uvPython"
                New-Venv $Venv $uvPython
                return $true
            }
            Write-Host 'uv could not provide Python 3.13 (no download allowed?). Looking for a Python instead.' -ForegroundColor Yellow
        }
        Write-Host '==> Creating the virtual environment'
        if (New-VenvWithFoundPython $Venv) { return $false }
        $path = Read-PythonPath
        if (-not $path) {
            throw ("No usable Python.`nInstall Python 3.13 from https://www.python.org/downloads/ or uv, which brings its own Python:`n" +
                '  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"')
        }
        New-Venv $Venv $path
        return $false
    }


    function Get-RunningApp {
        # Processes of this installation: the .exe launchers and the Python they start (other clones are left alone).
        param([string]$Venv)
        $scripts = (Join-Path $Venv 'Scripts').ToLowerInvariant()
        @(Get-Process -Name 'paperscan', 'paperscanw', 'python', 'pythonw' -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path.ToLowerInvariant().StartsWith($scripts) })
    }


    function Stop-RunningApp {
        # A running app locks its .exe launchers, so updating them would fail halfway.
        # Its window is closed like a click on the close button, so the app can ask before unsaved pages are lost.
        param([string]$Venv)
        $running = Get-RunningApp $Venv
        if (-not $running) { return }
        Write-Host "==> Closing the running $appName so it can be updated (answer its question if it asks)"
        foreach ($process in $running) { [void]$process.CloseMainWindow() }
        $running | Wait-Process -Timeout $closeTimeoutSeconds -ErrorAction SilentlyContinue
        if (Get-RunningApp $Venv) {
            throw "$appName is still running. Close it (and any paperscan command in a terminal), then run the installer again."
        }
    }


    function Install-Package {
        # Editable install with all extras, so "git pull" updates the app in place.
        param([string]$Root, [string]$Venv, [string]$VenvPython, [bool]$UseUv)
        Write-Host '==> Installing PaperScan and its dependencies'
        $target = "$Root[all]"
        if ($UseUv -or ($uvInstalled -and -not (Test-Path (Join-Path $Venv 'Scripts\pip.exe')))) {
            uv pip install --python $VenvPython -e $target
        }
        else {
            & $VenvPython -m pip install --disable-pip-version-check -e $target
        }
        if ($LASTEXITCODE -ne 0) { throw 'Installing the dependencies failed.' }
    }


    function Add-StartMenuEntry {
        # Start menu shortcut to the windowed launcher (no console window).
        param([string]$Venv, [string]$Root)
        $programs = [Environment]::GetFolderPath('Programs')
        $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $programs "$appName.lnk"))
        $shortcut.TargetPath = Join-Path $Venv 'Scripts\paperscanw.exe'
        $shortcut.WorkingDirectory = $Root
        $shortcut.Description = 'Phone photos to clean, searchable PDFs'
        $shortcut.IconLocation = Join-Path $Root 'src\paperscan\gui\resources\app_icon.ico'
        $shortcut.Save()
    }


    function Add-Command {
        # paperscan.cmd in ~\.local\bin, which is added to the user PATH if missing.
        param([string]$Venv)
        New-Item -ItemType Directory -Force -Path $commandFolder | Out-Null
        $exe = Join-Path $Venv 'Scripts\paperscan.exe'
        # cmd.exe reads .cmd files in the console (OEM) code page, so a path such as C:\Users\<name with
        # umlauts> only survives in that encoding. A path it cannot represent is reported.
        $shim = Join-Path $commandFolder 'paperscan.cmd'
        $line = "@`"$exe`" %*"
        Set-Content -Encoding Oem -Path $shim -Value $line
        if ((Get-Content -Encoding Oem -Path $shim) -ne $line) {
            Write-Host "    The paperscan command will not work: the console cannot represent its path ($exe). Use the Start menu entry." -ForegroundColor Yellow
        }
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $entries = @($userPath -split ';' | Where-Object { $_ })
        if ($entries -notcontains $commandFolder) {
            [Environment]::SetEnvironmentVariable('Path', (($entries + $commandFolder) -join ';'), 'User')
            Write-Host "    Added $commandFolder to your PATH (open a new terminal to use it)."
        }
    }


    try {
        $root = Get-ProjectFolder
        $venv = Join-Path $root '.venv'
        $venvPython = Join-Path $venv 'Scripts\python.exe'
        $createdWithUv = New-Environment $venv $venvPython
        Stop-RunningApp $venv
        Install-Package $root $venv $venvPython $createdWithUv

        Write-Host '==> Adding the Start menu entry and the paperscan command'
        Add-StartMenuEntry $venv $root
        Add-Command $venv

        $cli = Join-Path $venv 'Scripts\paperscan.exe'
        if (-not $SkipTools) {
            Write-Host '==> Checking the external tools (Tesseract, cjpegli, adb)'
            & $cli setup
            if ($LASTEXITCODE -ne 0) { Write-Host 'Some tools are still missing; run "paperscan setup" later.' -ForegroundColor Yellow }
        }
        & $cli doctor

        if (-not $NoStart) {
            Write-Host "==> Starting $appName"
            Start-Process -FilePath (Join-Path $venv 'Scripts\paperscanw.exe') -WorkingDirectory $root
        }
        Write-Host "Done. From now on: the Start menu entry, or paperscan in a new terminal." -ForegroundColor Green
        # Through iex the user's own terminal stays open anyway.
        if ($ScriptFolder) { Wait-BeforeClose $closeDelaySeconds }
    }
    catch {
        Write-Host $_.Exception.Message -ForegroundColor Yellow
        # Through iex there is no script to leave: exit would close the user's terminal.
        if ($ScriptFolder) {
            # Started by double-click or from Explorer the window would close at once; keep the message readable.
            if ([Environment]::UserInteractive) {
                try { Read-Host 'Press Enter to close' | Out-Null } catch { }
            }
            exit 1
        }
    }
} $NoStart.IsPresent $SkipTools.IsPresent $PSScriptRoot $Python
