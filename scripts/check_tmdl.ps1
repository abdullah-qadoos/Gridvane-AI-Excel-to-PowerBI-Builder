# Usage: powershell -File scripts/check_tmdl.ps1 -Folders <Project>.SemanticModel\definition [...]
# Loads each folder with the TMDL parser bundled in Power BI Desktop (the same one Desktop uses on open).
param([string[]]$Folders)
# Validate generated TMDL with Microsoft's own parser shipped in Power BI Desktop.
$global:pivotraBin = "C:\Program Files\Microsoft Power BI Desktop\bin"
$global:pivotraResolving = @{}
[AppDomain]::CurrentDomain.add_AssemblyResolve({
    param($sender, $eventArgs)
    $name = (New-Object System.Reflection.AssemblyName($eventArgs.Name)).Name
    if ($global:pivotraResolving.ContainsKey($name)) { return $null }
    $global:pivotraResolving[$name] = $true
    $candidate = Join-Path $global:pivotraBin ($name + ".dll")
    if (Test-Path $candidate) { return [System.Reflection.Assembly]::LoadFile($candidate) }
    return $null
})
$assembly = [System.Reflection.Assembly]::LoadFile((Join-Path $global:pivotraBin "Microsoft.PowerBI.Tabular.dll"))
try { $types = $assembly.GetTypes() } catch [System.Reflection.ReflectionTypeLoadException] { $types = $_.Exception.Types | Where-Object { $_ } }
$serializer = $types | Where-Object { $_.Name -eq "TmdlSerializer" } | Select-Object -First 1
if (-not $serializer) { "TmdlSerializer not found"; exit 2 }
$method = $serializer.GetMethods() | Where-Object { $_.Name -eq "DeserializeDatabaseFromFolder" -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
"Using $($serializer.FullName).$($method.Name)"
foreach ($folder in $Folders) {
    try {
        $database = $method.Invoke($null, @($folder))
        $model = $database.Model
        $measures = 0; foreach ($table in $model.Tables) { $measures += $table.Measures.Count }
        "OK    $folder -> tables: $($model.Tables.Count), measures: $measures, relationships: $($model.Relationships.Count)"
    } catch {
        $inner = $_.Exception
        while ($inner.InnerException) { $inner = $inner.InnerException }
        "FAIL  $folder -> $($inner.Message)"
    }
}
