param([switch]$Upload, [string]$Port = 'COM3')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& "$PSScriptRoot/configure-arduino.ps1"
$cli = Join-Path $PSScriptRoot 'tools/arduino-cli/arduino-cli.exe'
$fqbn = 'esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashSize=8M,PSRAM=opi,PartitionScheme=huge_app'
Copy-Item -LiteralPath firmware/lv_conf.h -Destination work/arduino-user/libraries/lv_conf.h -Force
& $cli compile --config-file arduino-cli.yaml --fqbn $fqbn --build-path work/build firmware/codex_display
if ($LASTEXITCODE -ne 0) { throw 'Firmware compilation failed' }
if ($Upload) {
    & $cli upload --config-file arduino-cli.yaml --fqbn $fqbn --port $Port --input-dir work/build firmware/codex_display
    if ($LASTEXITCODE -ne 0) { throw 'Firmware upload failed' }
}
