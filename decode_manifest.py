import zipfile
from pathlib import Path
from androguard.core.axml import AXMLPrinter
apk = Path('artifacts/en-manhwaread/src/en/manhwaread/build/outputs/apk/release/tachiyomi-en.manhwaread-v1.4.2-release.apk')
with zipfile.ZipFile(apk) as z:
    manifest_bytes = z.read('AndroidManifest.xml')
printer = AXMLPrinter(manifest_bytes)
print(printer.get_xml().decode('utf-8'))
