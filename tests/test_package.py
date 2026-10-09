import hashlib
from pathlib import Path
import re
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import build_site


class TestPackage(TestCase):
    def test_index_checksum_and_package_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(build_site, 'SITE', Path(folder)/'site'):
                build_site.main()
                index = (build_site.SITE/'index.yml').read_text()
                path = re.search(r'^  path: (.+)$', index, re.M).group(1)
                expected = re.search(r'^  sha256: ([a-f0-9]{64})$', index, re.M).group(1)
                archive = build_site.SITE/path
                self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), expected)
                with zipfile.ZipFile(archive) as bundle:
                    self.assertEqual(set(bundle.namelist()), {
                        'whisparrPerformerSync.yml', 'performer_sync.py', 'trigger_sync.py'})
                    manifest = bundle.read('whisparrPerformerSync.yml')
                    self.assertIn(b'interface: raw', manifest)
                    self.assertIn(f'version: {build_site.VERSION}'.encode(), manifest)
                self.assertIn('id: whisparrPerformerSync', index)
                self.assertIn(f'version: {build_site.VERSION}', index)
