param(
    [string]$SourceEnv = ".env",
    [string]$OutputFile = ".env.public",
    [switch]$IncludeAuth
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $SourceEnv)) {
    Write-Host "  [WARN] $SourceEnv not found. Creating supabase-auth $OutputFile"
    @(
        "# Vidnets Studio - Distribution Config",
        "# Users authenticate via Google OAuth through Supabase.",
        "# Service role key is NOT included.",
        "SUPABASE_URL=",
        "SUPABASE_ANON_KEY=",
        "GOOGLE_CLIENT_ID="
    ) | Set-Content -Path $OutputFile -Encoding UTF8
    exit 0
}

$envData = @{}
Get-Content $SourceEnv | ForEach-Object {
    if ($_ -match '^(SUPABASE_URL|SUPABASE_ANON_KEY|GOOGLE_CLIENT_ID)=(.*)') {
        $key = $matches[1]
        $val = $matches[2].Trim('"').Trim("'")
        $envData[$key] = $val
    }
}

$keys = @(
    'SUPABASE_URL',
    'SUPABASE_ANON_KEY',
    'GOOGLE_CLIENT_ID'
)

$lines = @(
    "# Vidnets Studio - Distribution Config",
    "# Users authenticate via Google OAuth through Supabase.",
    "# Service role key is NOT included."
)
foreach ($k in $keys) {
    if ($envData.ContainsKey($k) -and $envData[$k]) {
        $lines += "$k=$($envData[$k])"
    }
}

if ($lines.Count -eq 0) {
    Write-Host "  [WARN] No public config keys found in $SourceEnv. Creating minimal $OutputFile"
    $lines = @(
        "SUPABASE_URL=",
        "SUPABASE_ANON_KEY=",
        "GOOGLE_CLIENT_ID="
    )
}

$lines | Set-Content -Path $OutputFile -Encoding UTF8
Write-Host "  [OK] $OutputFile created with $($lines.Count) entries"

# Verify output
if ((Get-Item $OutputFile).Length -eq 0) {
    Write-Error "  [ERR] $OutputFile is empty!"
    exit 1
}
