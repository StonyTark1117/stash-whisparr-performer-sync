import io
import json
from pathlib import Path
import tempfile
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

    def test_unfavorited_create_hook_skips_before_api_access(self):
        payload = {'server_connection': CONNECTION,
                   'args': {'mode': 'hook', 'hookContext': {
                       'type': 'Performer.Create.Post', 'input': {'favorite': False}}}}
        with patch('sys.stdin', io.StringIO(json.dumps(payload))), \
             patch.object(plugin, 'configured_api') as configured:
            self.assertEqual(plugin.main(), {'skipped': 'New performer is not favorited'})
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

    def test_lock_rejects_concurrent_run_then_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'lock'
            with path.open('a+b') as first, path.open('a+b') as second:
                self.assertTrue(plugin.acquire_lock(first))
                self.assertFalse(plugin.acquire_lock(second))
            with path.open('a+b') as third:
                self.assertTrue(plugin.acquire_lock(third))

    def test_invalid_defaults_stop_sync_before_state_or_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = {'server_connection': dict(CONNECTION, Dir=folder),
                       'args': {'mode': 'sync'}}
            api = type('Fake', (), {})()
            api.c = {'sync_enabled': True, 'quality_profile_id': 6,
                     'root_folder': '/missing'}
            api.w = lambda endpoint: [{'id': 6}] if endpoint == 'qualityprofile' else [{'path': '/other'}]
            with patch('sys.stdin', io.StringIO(json.dumps(payload))), \
                 patch.object(plugin, 'configured_api', return_value=api):
                with self.assertRaisesRegex(ValueError, 'root folder'):
                    plugin.main()
            self.assertFalse((Path(folder)/'whisparr-performer-sync').exists())

    def test_favorite_update_hook_reaches_sync(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = {'server_connection': dict(CONNECTION, Dir=folder),
                       'args': {'mode': 'hook', 'hookContext': {
                           'type': 'Performer.Update.Post', 'inputFields': ['favorite']}}}
            api = type('Fake', (), {})()
            api.c = {'sync_enabled': True, 'quality_profile_id': 6,
                     'root_folder': '/media'}
            api.w = lambda endpoint: [{'id': 6}] if endpoint == 'qualityprofile' else [{'path': '/media'}]
            with patch('sys.stdin', io.StringIO(json.dumps(payload))), \
                 patch.object(plugin, 'configured_api', return_value=api), \
                 patch.object(plugin.Sync, 'run', return_value={'actions': [], 'problems': []}) as run:
                plugin.main()
            run.assert_called_once_with()

    def test_favorited_create_and_destroy_hooks_reach_sync(self):
        for kind, original_input in (
            ('Performer.Create.Post', {'favorite': True}),
            ('Performer.Destroy.Post', None),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                payload = {'server_connection': dict(CONNECTION, Dir=folder),
                           'args': {'mode': 'hook', 'hookContext': {
                               'type': kind, 'input': original_input}}}
                api = type('Fake', (), {})()
                api.c = {'sync_enabled': True, 'quality_profile_id': 6,
                         'root_folder': '/media'}
                api.w = lambda endpoint: [{'id': 6}] if endpoint == 'qualityprofile' else [{'path': '/media'}]
                with patch('sys.stdin', io.StringIO(json.dumps(payload))), \
                     patch.object(plugin, 'configured_api', return_value=api), \
                     patch.object(plugin.Sync, 'run', return_value={'actions': [], 'problems': []}) as run:
                    plugin.main()
                run.assert_called_once_with()
