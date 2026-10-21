$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot.Replace('\', '/')
$configuration = @{
    board_manager = @{ additional_urls = @('https://espressif.github.io/arduino-esp32/package_esp32_index.json') }
    directories = @{
        data = "$projectRoot/work/arduino-data"
        downloads = "$projectRoot/work/arduino-downloads"
        user = "$projectRoot/work/arduino-user"
    }
}
New-Item -ItemType Directory -Force -Path "$PSScriptRoot/work/arduino-data", "$PSScriptRoot/work/arduino-downloads", "$PSScriptRoot/work/arduino-user/libraries" | Out-Null
# JSON is valid YAML; using a structured serializer also handles spaces in paths.
$configuration | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$PSScriptRoot/arduino-cli.yaml" -Encoding utf8
