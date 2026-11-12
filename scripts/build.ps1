param([switch]$SkipSmoke)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Set-Location -LiteralPath $projectRoot
# PyInstaller replaces its own build folders; reject redirected output directories.
foreach ($folder in @('build', 'dist')) {
    $target = [IO.Path]::GetFullPath((Join-Path $projectRoot $folder))
    if (-not $target.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar)) {
        throw "Output path escaped the project: $target"
    }
    if ((Test-Path -LiteralPath $target) -and ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Output directory must not be a junction or symlink: $target"
    }
}
python -m uv sync --locked --python 3.11 --extra dev --extra llm --extra app --extra build
if ($LASTEXITCODE -ne 0) { throw 'Dependency synchronization failed.' }
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $pythonExe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& $pythonExe -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Lint failed.' }
& $pythonExe -m PyInstaller --clean --noconfirm sheetforge.spec
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
if (-not $SkipSmoke) {
    & $pythonExe scripts/smoke_exe.py dist/sheetforge/sheetforge.exe
    if ($LASTEXITCODE -ne 0) { throw 'Executable smoke test failed.' }
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $projectRoot 'dist\sheetforge\README.md')
Compress-Archive -LiteralPath (Join-Path $projectRoot 'dist\sheetforge') -DestinationPath (Join-Path $projectRoot 'dist\sheetforge-windows-x64.zip') -Force
Write-Output 'Portable build: dist\sheetforge\sheetforge.exe (keep the entire folder together).'
