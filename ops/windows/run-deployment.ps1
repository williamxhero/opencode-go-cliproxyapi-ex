$ErrorActionPreference = 'Stop'
$root='D:\WILL\AGENT\CPA\ops\opencode-spec2-upgrade'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$admin=([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Administrator token required; do not fall back to direct service manipulation.' }
$python='C:\Users\will\AppData\Local\hermes\installs\627beea1074921d0\environments\119be51e53cd45d0b961c26ba09b2604\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Pinned Python runtime missing.' }
$script=Join-Path $root 'deploy_plugin.py'
$manifest=Join-Path $root 'manifest.json'
$stdout=Join-Path $root 'deployment.stdout.log'
$stderr=Join-Path $root 'deployment.stderr.log'
$launcher=Join-Path $root 'deployment-launch.json'
$finished=Join-Path $root 'deployment-exit.json'
if (Test-Path -LiteralPath $finished) { throw 'Transaction was already run; use a new manifest and directory.' }
$p=Start-Process -FilePath $python -ArgumentList @($script,$manifest) -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
@{pid=$p.Id; start=[DateTime]::UtcNow.ToString('o'); elevated=$true} | ConvertTo-Json | Set-Content -LiteralPath $launcher -Encoding UTF8
$p.WaitForExit()
$exitCode=$p.ExitCode
@{pid=$p.Id; exitCode=$exitCode; finish=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $finished -Encoding UTF8
$resultPath=Join-Path $root 'result.json'
if (-not (Test-Path -LiteralPath $resultPath)) { throw 'Missing deployment result receipt.' }
$result=Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
if ($result.outcome -notin @('upgraded','rolled_back') -or -not $result.core_healthy) { throw 'Guarded deployment or recovery failed.' }
if ($null -ne $exitCode -and $exitCode -notin @(0,2)) { throw 'Guarded deployment returned a failure status.' }
