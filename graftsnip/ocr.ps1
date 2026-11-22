param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$OutPath
)
# Runs the Windows OCR engine (Windows.Media.Ocr) on an image and writes JSON lines + word boxes.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $null = $task.Wait(-1)
    $task.Result
}

try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) { throw 'No OCR language pack is installed for your Windows display language.' }
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    foreach ($line in $result.Lines) {
        $words = @()
        foreach ($w in $line.Words) {
            $r = $w.BoundingRect
            $words += , @{ t = $w.Text; x = $r.X; y = $r.Y; w = $r.Width; h = $r.Height }
        }
        $lines += , @{ text = $line.Text; words = $words }
    }
    $payload = @{ ok = $true; lines = $lines; max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension }
    $stream.Dispose()
}
catch {
    $payload = @{ ok = $false; error = $_.Exception.Message }
}
$json = ConvertTo-Json -InputObject $payload -Depth 8 -Compress
[System.IO.File]::WriteAllText($OutPath, $json, (New-Object System.Text.UTF8Encoding($false)))
