import zipfile
from pathlib import Path
apk = Path('artifacts/en-manhwaread/src/en/manhwaread/build/outputs/apk/release/tachiyomi-en.manhwaread-v1.4.2-release.apk')
with zipfile.ZipFile(apk) as z:
    dex_data = z.read('classes.dex')
print(len(dex_data))
