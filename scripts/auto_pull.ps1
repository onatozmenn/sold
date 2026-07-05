# sold — repo'yu GitHub'dan otomatik günceller (KFE verisi vb.).
# Windows Task Scheduler tarafından (oturum açılışında) çalıştırılır.
# GÜVENLİ: yalnızca fast-forward çeker; yerel çakışma/merge commit YARATMAZ.
#         Yerelde ayrık commit varsa sessizce başarısız olur (repoya zarar vermez).

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot   # scripts/ -> repo kökü
Set-Location -LiteralPath $repo

$log = Join-Path $repo "data\auto_pull.log"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] git pull --ff-only ($repo)" | Add-Content $log
try {
    $out = git pull --ff-only 2>&1
    $out | ForEach-Object { "  $_" } | Add-Content $log
} catch {
    "  HATA: $($_.Exception.Message)" | Add-Content $log
}
"" | Add-Content $log
