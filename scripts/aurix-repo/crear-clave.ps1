<#
  Crea la clave con la que se firma el repositorio de paquetes de Aurix. La ejecuta el DUEÑO, una sola vez.

  Hace cuatro cosas:
    1. Genera la clave (en una carpeta temporal que se borra al terminar).
    2. Guarda la copia de seguridad de la clave PRIVADA en tu carpeta de usuario (fuera de los repositorios).
    3. La sube como secreto APT_SIGNING_KEY de Auralix-Studio/aurix-packages (con «gh»).
    4. Escribe la clave PÚBLICA en aurix-packages y en aurix-code (esa sí va en git: la usan los teléfonos).

  Uso:  powershell -File scripts/aurix-repo/crear-clave.ps1
  Si pierdes la copia y el secreto, habría que cambiar la clave en la app: guárdala en tu gestor de contraseñas.
#>
param(
    [switch]$SinSubir,          # para ensayos: no toca GitHub
    [string]$Raiz = '',         # para ensayos: carpeta donde escribir en vez de los repositorios
    [string]$Copia = ''
)
$ErrorActionPreference = 'Stop'
$packages = Resolve-Path (Join-Path $PSScriptRoot '../..')
$code = Join-Path (Split-Path $packages -Parent) 'aurix-code'
if ($Raiz) { $packages = Join-Path $Raiz 'aurix-packages'; $code = Join-Path $Raiz 'aurix-code' }
if (-not $Copia) { $Copia = Join-Path $HOME 'aurix-apt-clave-PRIVADA.asc' }
$publicTargets = @(
    (Join-Path $packages 'packages/termux-keyring/aurix-packages.gpg'),
    (Join-Path $code 'app/src/main/assets/apt/aurix-packages.gpg')
)
if (Test-Path $Copia) { throw "Ya existe $Copia. No la sobrescribo: si de verdad quieres una clave nueva, muévela tú antes." }

$gpg = 'C:\Program Files\Git\usr\bin\gpg.exe'
if (-not (Test-Path $gpg)) { $gpg = (Get-Command gpg -ErrorAction Stop).Source }
$work = Join-Path ([IO.Path]::GetTempPath()) ('aurix-gpg-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $work | Out-Null
# El gpg que trae Git entiende rutas estilo /c/...; con rutas de Windows no arranca su agente.
function ConvertTo-UnixPath([string]$p) {
    if ($gpg -like '*\Git\*' -and $p -match '^([A-Za-z]):(.*)$') { return '/' + $Matches[1].ToLower() + ($Matches[2] -replace '\\', '/') }
    return $p
}
try {
    $ErrorActionPreference = 'Continue'   # gpg escribe avisos por stderr; los fallos se detectan por el código de salida
    & $gpg --homedir (ConvertTo-UnixPath $work) --batch --quiet --pinentry-mode loopback --passphrase '""' `
        --quick-generate-key 'Aurix packages (Auralix Studio)' rsa4096 sign 10y
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo generar la clave' }
    & $gpg --homedir (ConvertTo-UnixPath $work) --batch --quiet --pinentry-mode loopback --passphrase '""' `
        --armor --output (ConvertTo-UnixPath $Copia) --export-secret-keys
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo exportar la clave privada' }
    foreach ($target in $publicTargets) {
        New-Item -ItemType Directory -Force (Split-Path $target -Parent) | Out-Null
        if (Test-Path $target) { Remove-Item $target }
        & $gpg --homedir (ConvertTo-UnixPath $work) --batch --quiet --output (ConvertTo-UnixPath $target) --export
        if ($LASTEXITCODE -ne 0) { throw "No se pudo escribir $target" }
    }
    $ErrorActionPreference = 'Stop'
    if (-not $SinSubir) {
        Get-Content -Raw $Copia | gh secret set APT_SIGNING_KEY --repo Auralix-Studio/aurix-packages
        if ($LASTEXITCODE -ne 0) { throw "No se pudo guardar el secreto. La clave está en ${Copia}: súbela a mano." }
    }
    Write-Host ''
    Write-Host "Listo. Copia de la clave privada: $Copia"
    Write-Host 'Guárdala en tu gestor de contraseñas y borra el archivo. NUNCA la subas a git.'
    Write-Host "Clave pública escrita en:`n  $($publicTargets -join "`n  ")"
} finally {
    & (Join-Path (Split-Path $gpg -Parent) 'gpgconf.exe') --homedir (ConvertTo-UnixPath $work) --kill all 2>$null
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
