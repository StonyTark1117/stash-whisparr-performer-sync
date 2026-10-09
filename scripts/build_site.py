#!/usr/bin/env python3
"""Build an original, minimal Stash plugin-manager source index and zip."""
import datetime
import hashlib
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT/'dist'/'site'
PLUGIN = ROOT/'plugin'
VERSION = '1.0.1'
ARCHIVE = f'whisparrPerformerSync-{VERSION}.zip'


def main():
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    package = SITE/ARCHIVE
    with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in ('whisparrPerformerSync.yml', 'performer_sync.py', 'trigger_sync.py'):
            bundle.write(PLUGIN/name, name)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    day = datetime.date.today().isoformat()
    (SITE/'index.yml').write_text(
        f'- id: whisparrPerformerSync\n'
        f'  name: Whisparr Performer Sync\n'
        f'  version: {VERSION}\n'
        f'  date: {day}\n'
        f'  path: {ARCHIVE}\n'
        f'  sha256: {digest}\n')
    print(f'Built {package} ({digest})')


if __name__ == '__main__':
    main()
