# Regenerates scanned-hindi.png from expected.json: an A4 page at 150 dpi, a heading and three
# wrapped paragraphs. Windows only: GDI+ shapes Devanagari correctly (Pillow without libraqm
# does not) and Nirmala UI ships with Windows.
# Then: .venv/Scripts/python tests/fixtures/ocr/make_pdf.py   (image-only PDF, no text layer)
Add-Type -AssemblyName System.Drawing
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$data = [IO.File]::ReadAllText("$here\expected.json", [Text.Encoding]::UTF8) | ConvertFrom-Json
$bmp = New-Object System.Drawing.Bitmap 1240, 1754
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::White)
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$body = New-Object System.Drawing.Font("Nirmala UI", 28, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$head = New-Object System.Drawing.Font("Nirmala UI", 40, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$brush = [System.Drawing.Brushes]::Black
$y = 150
$first = $true
foreach ($line in $data.lines) {
    $font = if ($first) { $head } else { $body }
    $rect = New-Object System.Drawing.RectangleF 110, $y, 1020, 600
    $g.DrawString($line, $font, $brush, $rect)
    $size = $g.MeasureString($line, $font, 1020)
    $y += [int]$size.Height + 45
    $first = $false
}
$g.Dispose()
$bmp.Save("$here\scanned-hindi.png", [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
