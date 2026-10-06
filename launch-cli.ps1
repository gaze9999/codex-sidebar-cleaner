param([ValidateSet('zh-TW', 'en')][string]$Language = 'zh-TW')
$launcherPath = Join-Path $PSScriptRoot 'launch-cli.cmd'
& "$env:SystemRoot\System32\cmd.exe" /d /c $launcherPath $Language
exit $LASTEXITCODE
