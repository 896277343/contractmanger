$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
python -m pip install -r requirements-desktop.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ContractOA同步工具 desktop_sync.py
if ($LASTEXITCODE -ne 0) { throw 'EXE 打包失败' }
Write-Host '打包完成：dist\ContractOA同步工具.exe'
