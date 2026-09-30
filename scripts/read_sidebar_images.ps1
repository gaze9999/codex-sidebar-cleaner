param(
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [Parameter(Mandatory=$true, ValueFromRemainingArguments=$true)][string[]]$ImagePaths
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$taskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await-Ocr($operation, $type) {
    $task = $taskMethod.MakeGenericMethod($type).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'No Windows OCR language is available; use a verified titles file instead.' }
$records = @(foreach ($imagePath in $ImagePaths) {
    $path = (Resolve-Path -LiteralPath $imagePath -ErrorAction Stop).Path
    $file = Await-Ocr ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await-Ocr ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await-Ocr ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await-Ocr ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result = Await-Ocr ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $hasher = [Security.Cryptography.SHA256]::Create()
            $hashStream = [IO.File]::OpenRead($path)
            try { $hash = [BitConverter]::ToString($hasher.ComputeHash($hashStream)).Replace('-', '') }
            finally { $hashStream.Dispose(); $hasher.Dispose() }
            [pscustomobject]@{name=[IO.Path]::GetFileName($path); sha256=$hash;
                language=$engine.RecognizerLanguage.LanguageTag; lines=@($result.Lines | ForEach-Object {$_.Text})}
        } finally { $bitmap.Dispose() }
    } finally { $stream.Dispose() }
})
ConvertTo-Json -InputObject $records -Depth 6 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
Write-Output ('Images processed locally: ' + $records.Count)
