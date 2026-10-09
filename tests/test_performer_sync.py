import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from performer_sync import API, Sync, STASHDB, TPDB, desired, profile_patch, stash_connection


def wp(fid='abc', monitored=True, name='Example Performer'):
    return {'id': 1, 'foreignId': fid, 'monitored': monitored, 'name': name,
            'gender': 'female', 'qualityProfileId': 6, 'rootFolderPath': '/media/stash',
            'moviesMonitored': True, 'whisparrMonitorNewItems': True, 'searchOnAdd': False}


def sp(fid='abc', favorite=True, name='Example Performer'):
    return {'id': '1', 'name': name, 'gender': 'FEMALE', 'favorite': favorite,
            'stash_ids': [{'endpoint': STASHDB, 'stash_id': fid}] if fid else []}


class FakeAPI:
    def __init__(self):
        self.ws, self.ss, self.calls = [], [], []
        self.fail_read = self.fail_stash = False
        self.c = {'quality_profile_id': 6, 'root_folder': '/media/stash'}

    def w(self, path, method='GET', data=None):
        self.calls.append((path, method, copy.deepcopy(data)))
        if path == 'performer' and method == 'GET':
            return copy.deepcopy(self.ws)
        if path == 'performer' and method == 'POST':
            w = dict(data, id=len(self.ws)+1, name=data['fullName'], gender='female')
            self.ws.append(w)
            return copy.deepcopy(w)
        identifier = path.split('/')[1]
        w = next(w for w in self.ws if identifier in (str(w['id']), w['foreignId']))
        if method == 'PUT':
            w.update(data)
        return copy.deepcopy(w)

    def stash_all(self):
        if self.fail_read:
            raise RuntimeError('offline')
        return copy.deepcopy(self.ss)

    def stash_get(self, identifier):
        return copy.deepcopy(next((s for s in self.ss if s['id'] == identifier), None))

    def stash_update(self, data):
        if self.fail_stash:
            raise RuntimeError('offline')
        s = next(s for s in self.ss if s['id'] == data['id'])
        s.update(data)
        return copy.deepcopy(s)

    def stash_create(self, w):
        s = sp(w['foreignId'], True, w['name'])
        s['id'] = str(len(self.ss)+1)
        self.ss.append(s)
        return copy.deepcopy(s)

    def enrich(self, identifier, w):
        return {'fields_filled': ['image', 'country'], 'image': True}


class TestSync(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'state.json'
        self.api = FakeAPI()

    def run_sync(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return Sync(self.api, self.path).run()

    def test_whisparr_creates_favorite_and_is_idempotent(self):
        self.api.ws = [wp()]
        self.assertEqual(len(self.run_sync()['actions']), 2)
        self.assertTrue(self.api.ss[0]['favorite'])
        self.assertEqual(self.api.ss[0]['stash_ids'][0]['stash_id'], 'abc')
        self.assertEqual(self.run_sync()['actions'], [])
        self.assertEqual(len(self.api.ss), 1)

    def test_stash_creates_whisparr_without_immediate_search(self):
        self.api.ss = [sp()]
        self.run_sync()
        self.assertTrue(self.api.ws[0]['monitored'])
        self.assertFalse(self.api.ws[0]['searchOnAdd'])
        self.assertEqual(self.api.ws[0]['qualityProfileId'], 6)

    def test_both_directions_and_preserve_other_monitoring_preferences(self):
        self.api.ws, self.api.ss = [wp()], [sp()]
        self.run_sync()
        self.api.ss[0]['favorite'] = False
        self.run_sync()
        self.assertFalse(self.api.ws[0]['monitored'])
        self.assertTrue(self.api.ws[0]['moviesMonitored'])
        self.api.ws[0]['monitored'] = True
        self.run_sync()
        self.assertTrue(self.api.ss[0]['favorite'])
        self.api.ws[0]['monitored'] = False
        self.run_sync()
        self.assertFalse(self.api.ss[0]['favorite'])
        self.api.ss[0]['favorite'] = True
        self.run_sync()
        self.assertTrue(self.api.ws[0]['monitored'])

    def test_deleted_stash_record_unmonitors_without_recreation(self):
        self.api.ws, self.api.ss = [wp()], [sp()]
        self.run_sync()
        self.api.ss = []
        self.run_sync()
        self.assertFalse(self.api.ws[0]['monitored'])
        self.assertEqual(self.api.ss, [])
        self.assertEqual(self.run_sync()['actions'], [])

    def test_deleted_whisparr_record_unfavorites_without_recreation(self):
        self.api.ws, self.api.ss = [wp()], [sp()]
        self.run_sync()
        self.api.ws = []
        self.run_sync()
        self.assertFalse(self.api.ss[0]['favorite'])
        self.assertEqual(self.api.ws, [])

    def test_outage_cannot_be_interpreted_as_removal(self):
        self.api.ws, self.api.ss = [wp()], [sp()]
        self.run_sync()
        old = self.path.read_text()
        self.api.fail_read = True
        with self.assertRaises(RuntimeError):
            self.run_sync()
        self.assertTrue(self.api.ws[0]['monitored'])
        self.assertEqual(old, self.path.read_text())

    def test_failed_mutation_retries_and_state_survives_restart(self):
        self.api.ws, self.api.ss = [wp()], [sp()]
        self.run_sync()
        self.api.ws[0]['monitored'] = False
        self.api.fail_stash = True
        self.assertTrue(self.run_sync()['problems'])
        self.assertTrue(json.loads(self.path.read_text())['pairs']['abc']['s'])
        self.api.fail_stash = False
        self.run_sync()
        self.assertFalse(self.api.ss[0]['favorite'])

    def test_duplicate_identity_requires_review(self):
        self.api.ws = [wp()]
        self.api.ss = [sp(), dict(sp(), id='2')]
        self.assertTrue(self.run_sync()['problems'])
        self.assertFalse(self.path.exists())

    def test_unlinked_unique_name_links_and_unknown_name_defers(self):
        self.api.ws, self.api.ss = [wp(monitored=False)], [sp(fid=None)]
        self.run_sync()
        self.assertTrue(self.api.ws[0]['monitored'])
        self.assertEqual(self.api.ss[0]['stash_ids'][0]['stash_id'], 'abc')
        self.api.ss.append(dict(sp(fid=None, name='Unknown Performer'), id='2'))
        self.assertTrue(self.run_sync()['problems'])
        self.assertEqual(len(self.api.ws), 1)

    def test_tpdb_identity_and_male_exclusion(self):
        self.api.c['excluded_genders'] = {'MALE', 'TRANSGENDER_MALE'}
        self.api.ws = [dict(wp(monitored=False), tpdbId='tpdb-id')]
        self.api.ss = [dict(sp(), stash_ids=[{'endpoint': TPDB, 'stash_id': 'tpdb-id'}])]
        self.run_sync()
        self.assertTrue(self.api.ws[0]['monitored'])
        self.api.ws.append(dict(wp(fid='male'), id=2, gender='male'))
        self.assertTrue(self.run_sync()['problems'])
        self.assertEqual(len(self.api.ss), 1)

    def test_public_default_includes_all_genders(self):
        self.api.ws = [dict(wp(fid='male'), gender='male')]
        self.assertFalse(self.run_sync()['problems'])
        self.assertEqual(len(self.api.ss), 1)

    def test_preview_is_read_only(self):
        self.api.ws = [wp()]
        before = copy.deepcopy(self.api.ws)
        report = Sync(self.api, self.path).run(preview=True)
        self.assertTrue(report['preview'])
        self.assertEqual(report['actions'][0]['action'], 'would_create_stash_favorite')
        self.assertEqual(self.api.ws, before)
        self.assertEqual(self.api.ss, [])
        self.assertFalse(self.path.exists())

    def test_cookie_connection(self):
        connection = {'Scheme': 'http', 'Port': 9999,
                      'SessionCookie': {'Name': 'session', 'Value': 'abc'}}
        result = stash_connection(connection)
        self.assertEqual(result['stash_cookie'], 'session=abc')
        self.assertEqual(result['stash_url'], 'http://127.0.0.1:9999')

    def test_metadata_fills_gaps_preserves_edits_and_favorite(self):
        p = profile_patch({'name': 'Provider name', 'country': 'US', 'height': '170',
                           'images': ['https://example.test/portrait.jpg'], 'aliases': 'A, B',
                           'urls': ['https://example.test/profile']},
                          {'name': 'Manual name', 'favorite': False, 'alias_list': ['A', 'Manual'],
                           'image_path': '/performer/1/image?default=true'})
        self.assertNotIn('name', p)
        self.assertNotIn('favorite', p)
        self.assertEqual(p['height_cm'], 170)
        self.assertEqual(p['alias_list'], ['A', 'Manual', 'B'])
        self.assertIn('image', p)

    def test_creation_fetches_profile_before_any_mutation(self):
        api = API({})
        calls = []
        def offline(w):
            raise RuntimeError('offline')
        api.profile = offline
        api.gql = lambda *a, **kw: calls.append(a)
        with self.assertRaises(RuntimeError):
            api.stash_create(wp())
        self.assertEqual(calls, [])

    def test_new_creation_includes_metadata_and_image(self):
        api = API({})
        api.profile = lambda w: {'name': 'Example Performer', 'country': 'US',
                                 'images': ['https://example.test/portrait.jpg'], 'height': '170'}
        api.stash_all = lambda: []
        captured = []
        def gql(q, v):
            captured.append(v['input'])
            return {'performerCreate': {'id': '1', 'favorite': True}}
        api.gql = gql
        api.stash_create(wp())
        self.assertEqual(captured[0]['country'], 'US')
        self.assertIn('image', captured[0])
        self.assertEqual(captured[0]['stash_ids'][0]['stash_id'], 'abc')

    def test_identify_record_created_during_scrape_is_reused(self):
        api = API({})
        api.profile = lambda w: {'name': 'Example Performer', 'images': ['https://example.test/image']}
        api.stash_all = lambda: [dict(sp(), id='99', favorite=False)]
        api.enrich = lambda *args: None
        api.stash_update = lambda d: dict(d)
        def unexpected(*args):
            self.fail('Must not create a duplicate performer')
        api.gql = unexpected
        result = api.stash_create(wp())
        self.assertEqual(result, {'id': '99', 'favorite': True})

    def test_enrichment_failure_does_not_lose_removal_baseline(self):
        self.api.ws = [wp()]
        def fail(*args):
            raise RuntimeError('metadata offline')
        self.api.enrich = fail
        self.assertTrue(self.run_sync()['problems'])
        self.assertTrue(json.loads(self.path.read_text())['pairs']['abc']['s'])
        self.api.ss[0]['favorite'] = False
        self.run_sync()
        self.assertFalse(self.api.ws[0]['monitored'])


if __name__ == '__main__':
    unittest.main()
