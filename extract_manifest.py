import zipfile
from pathlib import Path
apk = Path('artifacts/en-manhwaread/src/en/manhwaread/build/outputs/apk/release/tachiyomi-en.manhwaread-v1.4.2-release.apk')
with zipfile.ZipFile(apk) as z:
    manifest = z.read('AndroidManifest.xml')
print(len(manifest))
