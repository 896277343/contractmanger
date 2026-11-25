param(
    [Parameter(Position = 0)]
    [string]$Message = "Update contract manager"
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $project

if (-not (Test-Path ".git")) {
    throw "当前目录尚未连接 Git 仓库；请先完成首次 GitHub 设置。"
}
git remote get-url origin *> $null
if ($LASTEXITCODE -ne 0) {
    throw "未设置 origin 远程仓库。"
}

git add -- .gitignore .gitattributes README.md requirements.txt manage.py config contracts templates scripts
if ($LASTEXITCODE -ne 0) { throw "暂存代码失败。" }
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw "提交前检查失败。" }

$staged = @(git diff --cached --name-only)
$private = @($staged | Where-Object {
    $_ -match '(^|/)(data|staticfiles|\.venv[^/]*)(/|$)' -or
    $_ -match '(^|/)\.env[^/]*(/|$)' -or
    $_ -match '\.(sqlite3|db|log)(-|$)'
})
if ($private.Count -gt 0) {
    throw "发现不应提交的文件：$($private -join ', ')"
}
if ($staged.Count -eq 0) {
    Write-Host "没有代码变更，无需提交。"
    exit 0
}

git commit -m $Message
if ($LASTEXITCODE -ne 0) { throw "Git 提交失败，请检查 user.name 和 user.email。" }
git push -u origin HEAD
if ($LASTEXITCODE -ne 0) { throw "提交已保存在本地，但推送 GitHub 失败。" }
Write-Host "代码已提交并推送；数据库和密钥未加入提交。"
