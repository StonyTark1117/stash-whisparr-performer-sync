import io
import json
from unittest import TestCase
from unittest.mock import patch

import performer_sync as plugin


CONNECTION = {'Scheme': 'http', 'Port': 9999,
              'SessionCookie': {'Name': 'session', 'Value': 'abc'}, 'Dir': '/unused'}


class TestPluginEntry(TestCase):
    def test_unrelated_update_hook_skips_before_api_access(self):
        payload = {'server_connection': CONNECTION,
                   'args': {'mode': 'hook', 'hookContext': {
                       'type': 'Performer.Update.Post', 'inputFields': ['name']}}}
        with patch('sys.stdin', io.StringIO(json.dumps(payload))), \
             patch.object(plugin, 'configured_api') as configured:
            self.assertEqual(plugin.main(), {'skipped': 'Unrelated performer update'})
            configured.assert_not_called()

    def test_settings_are_validated_and_default_all_genders(self):
        settings = {'sync_enabled': True, 'whisparr_url': 'http://example.test:6969/',
                    'whisparr_api_key': 'secret', 'root_folder': '/media',
                    'quality_profile_id': 6}
        with patch.object(plugin, 'plugin_settings', return_value=settings):
            api = plugin.configured_api(CONNECTION)
        self.assertEqual(api.c['whisparr_url'], 'http://example.test:6969')
        self.assertEqual(api.c['excluded_genders'], set())
        self.assertTrue(api.c['sync_enabled'])

    def test_unknown_exclusion_rejected(self):
        settings = {'sync_enabled': True, 'whisparr_url': 'http://example.test',
                    'whisparr_api_key': 'secret', 'root_folder': '/media',
                    'quality_profile_id': 6, 'excluded_genders': 'UNKNOWN'}
        with patch.object(plugin, 'plugin_settings', return_value=settings):
            with self.assertRaises(ValueError):
                plugin.configured_api(CONNECTION)
