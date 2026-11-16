#requires -Version 5.1
<#
.SYNOPSIS
    AI① FastAPI 서버 + ngrok 고정 도메인 터널을 점검/기동/중지한다.

.DESCRIPTION
    - ngrok Authtoken 은 로컬 설정(%LOCALAPPDATA%\ngrok\ngrok.yml)을 그대로 사용한다.
      이 스크립트와 저장소에는 토큰을 저장하지 않는다.
    - AI① API Key 는 환경변수 AI1_INTERNAL_API_KEYS 또는 -KeyFile 에서만 읽고,
      어떤 경우에도 값을 출력하지 않는다.
    - 이미 8001 리스너나 같은 도메인의 터널이 떠 있으면 중복 기동하지 않는다.
      교체가 필요할 때만 -Restart 를 쓴다.

.PARAMETER Action
    Check(기본) | Start | Stop

.PARAMETER Domain
    고정 도메인. 기본값 vividness-pep-unboxed.ngrok-free.dev

.PARAMETER KeyFile
    API Key 파일 경로. 기본값 %TEMP%\ai1_internal_key.txt

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start_ai1_tunnel.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start_ai1_tunnel.ps1 -Action Start

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start_ai1_tunnel.ps1 -Action Start -Restart

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start_ai1_tunnel.ps1 -Action Stop
#>
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Start', 'Stop')]
    [string]$Action = 'Check',

    [string]$Domain = 'vividness-pep-unboxed.ngrok-free.dev',

    [string]$ListenHost = '127.0.0.1',

    [int]$Port = 8001,

    [string]$KeyFile,

    [string]$NgrokPath,

    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$script:failures = 0

function Write-Step {
    param([string]$Message)
    Write-Host "[ai1] $Message"
}

function Write-Fail {
    param([string]$Message)
    $script:failures++
    Write-Host "[ai1][FAIL] $Message" -ForegroundColor Red
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[ai1][ OK ] $Message" -ForegroundColor Green
}

function Get-RepoRoot {
    return (Split-Path -Parent $PSScriptRoot)
}

function Get-NgrokExe {
    if ($NgrokPath) {
        if (Test-Path $NgrokPath) { return $NgrokPath }
        throw "지정한 ngrok 경로가 없습니다: $NgrokPath"
    }
    $cmd = Get-Command ngrok -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $pkg = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe'
    if (Test-Path $pkg) { return $pkg }
    throw 'ngrok.exe 를 찾지 못했습니다. winget install ngrok.ngrok 또는 -NgrokPath 로 지정하세요.'
}

function Get-PortOwner {
    param([int]$LocalPort)
    $conn = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return 0
}

function Get-TunnelState {
    try {
        return (Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 3)
    } catch {
        return $null
    }
}

function Test-Http {
    param([string]$Url, [switch]$Ngrok)
    $headers = @{}
    if ($Ngrok) { $headers['ngrok-skip-browser-warning'] = 'true' }
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec 10 -UseBasicParsing -Headers $headers
        return [pscustomobject]@{ Ok = $true; Status = [int]$r.StatusCode; Body = $r.Content }
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        return [pscustomobject]@{ Ok = $false; Status = $status; Body = '' }
    }
}

function Get-ApiKey {
    if ($env:AI1_INTERNAL_API_KEYS -and $env:AI1_INTERNAL_API_KEYS.Trim()) {
        return $env:AI1_INTERNAL_API_KEYS.Trim()
    }
    $path = if ($KeyFile) { $KeyFile } else { Join-Path $env:TEMP 'ai1_internal_key.txt' }
    if (Test-Path $path) { return (Get-Content $path -Raw).Trim() }
    return $null
}

function Stop-Ai1Processes {
    $stopped = @()

    $uvicornPids = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -like '*app.main:app*' } |
        Select-Object -ExpandProperty ProcessId
    foreach ($p in $uvicornPids) {
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        $stopped += "uvicorn($p)"
    }

    $ngrokPid = Get-PortOwner -LocalPort 4040
    if ($ngrokPid -gt 0) {
        Stop-Process -Id $ngrokPid -Force -ErrorAction SilentlyContinue
        $stopped += "ngrok($ngrokPid)"
    }

    if ($stopped.Count -eq 0) {
        Write-Step '중지할 프로세스가 없습니다.'
    } else {
        Write-Step ("중지: " + ($stopped -join ', '))
        Start-Sleep -Seconds 2
    }
}

function Start-Uvicorn {
    $repo = Get-RepoRoot
    $python = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { throw "venv python 이 없습니다: $python" }

    $key = Get-ApiKey
    if ($key) {
        $env:AI1_INTERNAL_API_KEYS = $key
        Write-Ok 'API Key 를 환경변수로 주입했습니다(값 비출력).'
    } else {
        Write-Host '[ai1][WARN] API Key 를 찾지 못했습니다. 보호 경로는 503 E_AUTH_NOT_CONFIGURED 로 응답합니다.' -ForegroundColor Yellow
    }

    $out = Join-Path $env:TEMP 'ai1_uvicorn_8001.log'
    $err = Join-Path $env:TEMP 'ai1_uvicorn_8001.err'
    $proc = Start-Process -FilePath $python `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', $ListenHost, '--port', "$Port", '--log-level', 'warning' `
        -WorkingDirectory $repo -RedirectStandardOutput $out -RedirectStandardError $err `
        -PassThru -WindowStyle Hidden

    Write-Step "uvicorn 시작: pid=$($proc.Id) ($ListenHost`:$Port)"

    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        $r = Test-Http "http://127.0.0.1:$Port/health"
        if ($r.Status -eq 200) { Write-Ok '로컬 /health 200'; return }
    }
    Write-Fail "uvicorn 이 20초 안에 응답하지 않았습니다. 로그: $err"
}

function Start-Tunnel {
    $exe = Get-NgrokExe
    $out = Join-Path $env:TEMP 'ai1_ngrok.log'
    $err = Join-Path $env:TEMP 'ai1_ngrok.err'

    $proc = Start-Process -FilePath $exe `
        -ArgumentList 'http', "$Port", '--url', "https://$Domain", '--log', 'stdout' `
        -RedirectStandardOutput $out -RedirectStandardError $err `
        -PassThru -WindowStyle Hidden

    Write-Step "ngrok 시작: pid=$($proc.Id) -> https://$Domain"

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        $state = Get-TunnelState
        if ($state) {
            $url = ($state.tunnels | Where-Object { $_.public_url -like 'https://*' } |
                Select-Object -First 1).public_url
            if ($url) {
                if ($url -eq "https://$Domain") {
                    Write-Ok "터널 고정 도메인 일치: $url"
                } else {
                    Write-Fail "터널 도메인이 다릅니다: $url (기대 https://$Domain)"
                }
                return
            }
        }
    }
    Write-Fail "ngrok 터널이 30초 안에 올라오지 않았습니다. 로그: $err"
}

function Show-Check {
    Write-Step "고정 도메인: https://$Domain"
    $repo = Get-RepoRoot
    Write-Step "저장소: $repo"

    $key = Get-ApiKey
    if ($key) { Write-Ok 'API Key: 설정됨(값 비출력)' } else { Write-Fail 'API Key: 없음 → 보호 경로 503' }

    $listenPid = Get-PortOwner -LocalPort $Port
    if ($listenPid -gt 0) {
        Write-Ok "포트 $Port 리스너: pid=$listenPid"
        $r = Test-Http "http://127.0.0.1:$Port/health"
        if ($r.Status -eq 200) { Write-Ok "로컬 /health 200 ($($r.Body))" }
        else { Write-Fail "로컬 /health 응답: $($r.Status)" }
    } else {
        Write-Fail "포트 $Port 리스너 없음 (서버 미기동)"
    }

    $state = Get-TunnelState
    if (-not $state) {
        Write-Fail 'ngrok 로컬 API(127.0.0.1:4040) 응답 없음 (터널 미기동)'
        return
    }
    $tunnel = $state.tunnels | Where-Object { $_.public_url -like 'https://*' } | Select-Object -First 1
    if (-not $tunnel) { Write-Fail 'HTTPS 터널 없음'; return }

    Write-Step "터널: $($tunnel.public_url) -> $($tunnel.config.addr)"
    if ($tunnel.public_url -eq "https://$Domain") { Write-Ok '터널이 고정 도메인을 사용 중' }
    else { Write-Fail "터널이 고정 도메인과 다름: $($tunnel.public_url)" }

    $public = Test-Http "https://$Domain/health" -Ngrok
    if ($public.Status -eq 200 -and $public.Body -match '"status"\s*:\s*"ok"') {
        Write-Ok "공개 /health 200 ($($public.Body.Trim()))"
    } elseif ($public.Status -eq 200) {
        Write-Fail '공개 /health 200 이지만 본문이 JSON 이 아닙니다(ngrok 안내 페이지 가능성)'
    } else {
        Write-Fail "공개 /health 응답: $($public.Status)"
    }
}

try {
    switch ($Action) {
        'Stop' {
            Stop-Ai1Processes
        }
        'Start' {
            if ($Restart) { Stop-Ai1Processes }

            $listenPid = Get-PortOwner -LocalPort $Port
            if ($listenPid -gt 0) {
                Write-Step "포트 $Port 이미 사용 중(pid=$listenPid) — 서버 재기동 생략"
            } else {
                Start-Uvicorn
            }

            $state = Get-TunnelState
            if ($state) {
                $url = ($state.tunnels | Where-Object { $_.public_url -like 'https://*' } |
                    Select-Object -First 1).public_url
                if ($url -eq "https://$Domain") {
                    Write-Step "터널 이미 실행 중($url) — 재기동 생략"
                } else {
                    Write-Fail "다른 주소의 터널이 실행 중입니다: $url"
                    Write-Step '-Restart 로 교체할 수 있습니다.'
                }
            } else {
                Start-Tunnel
            }

            Show-Check
        }
        default {
            Show-Check
        }
    }
} catch {
    Write-Fail $_.Exception.Message
}

if ($script:failures -gt 0) { exit 1 }
exit 0
