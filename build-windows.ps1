$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
$python = '.\.venv\Scripts\python.exe'
& $python -m pip install -r requirements.txt 'pyinstaller>=6,<7'
if ($LASTEXITCODE -ne 0) { throw 'Could not install build dependencies.' }

& $python -m PyInstaller --clean --noconfirm --onefile --console --name SessionNotes `
    --paths app `
    --add-data 'app\index.html;app' `
    --add-data 'app\app.js;app' `
    --add-data 'app\style.css;app' `
    --hidden-import docx --hidden-import pypdf `
    app\desktop.py
if ($LASTEXITCODE -ne 0) { throw 'Windows build failed.' }
Write-Host 'Build complete: dist\SessionNotes.exe'
