$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe -c "from desktop_app import icon_image; from pathlib import Path; Path('work').mkdir(exist_ok=True); icon_image().save('work/desktop.ico', sizes=[(16,16),(32,32),(48,48),(64,64)])"
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed' }
$iconPath = Join-Path $PSScriptRoot 'work/desktop.ico'
$buildPath = $env:PATH
try {
    # Avoid collecting incompatible ICU/UCRT libraries from unrelated tool runtimes.
    $env:PATH = "$PSScriptRoot\.venv\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
    & .\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm --onefile --windowed --name CodexUsageDisplay --icon $iconPath --distpath dist --workpath work/desktop-build --specpath work --paths . --collect-submodules bleak --collect-submodules winrt desktop_app.py
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed' }
} finally {
    $env:PATH = $buildPath
}
