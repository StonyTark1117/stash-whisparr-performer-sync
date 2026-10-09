#!/usr/bin/env python3
"""Stash plugin: synchronize favorite performers with Whisparr Eros v3 monitoring."""
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

STASHDB = 'https://stashdb.org/graphql'
TPDB = 'https://theporndb.net/graphql'
GENDERS = {'female': 'FEMALE', 'male': 'MALE', 'transFemale': 'TRANSGENDER_FEMALE',
           'transMale': 'TRANSGENDER_MALE', 'intersex': 'INTERSEX', 'nonBinary': 'NON_BINARY'}
PLUGIN_ID = 'whisparrPerformerSync'
VALID_GENDERS = {'FEMALE', 'MALE', 'TRANSGENDER_FEMALE', 'TRANSGENDER_MALE',
                 'INTERSEX', 'NON_BINARY'}
PROFILE_FIELDS = ('name disambiguation gender birthdate ethnicity country eye_color height_cm '
                  'measurements fake_tits career_start career_end tattoos piercings details '
                  'death_date hair_color weight alias_list urls image_path stash_ids{endpoint stash_id}')
SCRAPED_FIELDS = ('remote_site_id name disambiguation gender birthdate ethnicity country eye_color '
                  'height measurements fake_tits career_start career_end tattoos piercings aliases '
                  'images urls details death_date hair_color weight')


def profile_patch(scraped, current=None):
    """Fill gaps and merge lists, preserving existing edits and favorite state."""
    current = current or {}
    result = {}
    fields = ('name disambiguation gender birthdate ethnicity country eye_color measurements '
              'fake_tits career_start career_end tattoos piercings details death_date hair_color').split()
    for key in fields:
        if scraped.get(key) and not current.get(key):
            result[key] = scraped[key]
    for source, dest in [('height', 'height_cm'), ('weight', 'weight')]:
        value = scraped.get(source)
        if value and not current.get(dest):
            try:
                result[dest] = int(value)
            except (TypeError, ValueError):
                pass
    for key, incoming in [('alias_list', [x.strip() for x in (scraped.get('aliases') or '').split(',') if x.strip()]),
                          ('urls', scraped.get('urls') or [])]:
        merged = list(dict.fromkeys((current.get(key) or []) + incoming))
        if merged and merged != current.get(key):
            result[key] = merged
    if scraped.get('images') and (not current.get('image_path') or 'default=true' in current['image_path']):
        result['image'] = scraped['images'][0]
    return result


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    with temp.open('w') as f:
        json.dump(value, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def acquire_lock(lock):
    """Try to hold a process lock until the file object is closed."""
    if os.name == 'nt':
        import msvcrt
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b'\0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
    else:
        import fcntl
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def desired(previous, s, w):
    if previous is None:
        return s or w  # Initial union preserves existing choices.
    ds, dw = s != previous['s'], w != previous['w']
    if ds and dw:
        return s if s == w else False  # Concurrent conflict: removal wins.
    if ds:
        return s
    if dw:
        return w
    return s if s == w else s or w


class API:
    def __init__(self, config):
        self.c = config
        self.profiles = {}

    def request(self, url, key, method='GET', data=None, stash=False):
        headers = {'Content-Type': 'application/json'}
        if stash:
            cookie = self.c.get('stash_cookie')
            if cookie:
                headers['Cookie'] = cookie
            else:
                headers['ApiKey'] = key
        else:
            headers['X-Api-Key'] = key
        req = urllib.request.Request(url, method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            # Do not put request headers, credentials, or response bodies in journals.
            raise RuntimeError(f'API HTTP {e.code}') from None

    def w(self, path, method='GET', data=None):
        return self.request(self.c['whisparr_url']+'/api/v3/'+path,
                            self.c['whisparr_api_key'], method, data)

    def gql(self, query, variables=None):
        d = self.request(self.c['stash_url']+'/graphql', self.c.get('stash_api_key'), 'POST',
                         {'query': query, 'variables': variables or {}}, stash=True)
        if d.get('errors'):
            raise RuntimeError('Stash GraphQL operation failed')
        return d['data']

    def stash_all(self):
        result = []
        page = 1
        while True:
            data = self.gql('''query($page:Int!){findPerformers(filter:{page:$page,per_page:500,sort:"id",direction:ASC}){
              count performers{id name disambiguation favorite gender stash_ids{endpoint stash_id}}
            }}''', {'page': page})['findPerformers']
            result.extend(data['performers'])
            if len(result) >= data['count']:
                return result
            if not data['performers']:
                raise RuntimeError('Incomplete Stash performer listing')
            page += 1

    def stash_update(self, data):
        return self.gql('''mutation($input:PerformerUpdateInput!){performerUpdate(input:$input){id favorite}}''',
                        {'input': data})['performerUpdate']

    def stash_get(self, identifier):
        return self.gql('query($id:ID!){findPerformer(id:$id){id favorite}}',
                        {'id': identifier})['findPerformer']

    def stash_detail(self, identifier):
        return self.gql('query($id:ID!){findPerformer(id:$id){id favorite '+PROFILE_FIELDS+'}}',
                        {'id': identifier})['findPerformer']

    def profile(self, w):
        fid = w['foreignId']
        if fid not in self.profiles:
            data = self.gql('''query($source:ScraperSourceInput!,$input:ScrapeSinglePerformerInput!){
              scrapeSinglePerformer(source:$source,input:$input){'''+SCRAPED_FIELDS+'''}}''',
              {'source': {'stash_box_endpoint': STASHDB}, 'input': {'query': fid}})
            matches = [p for p in data['scrapeSinglePerformer'] if p['remote_site_id'] == fid]
            if len(matches) != 1:
                raise RuntimeError('Provider metadata has no unique exact ID match')
            if matches[0].get('gender') in self.c.get('excluded_genders', set()):
                raise RuntimeError('Provider metadata has excluded performer gender')
            self.profiles[fid] = matches[0]
        return self.profiles[fid]

    def enrich(self, identifier, w):
        current = self.stash_detail(identifier)
        if current is None:
            raise RuntimeError('Performer removed while enriching; retry next cycle')
        profile = self.profile(w)
        patch = profile_patch(profile, current)
        ids = current['stash_ids'][:]
        for item in ids_from_w(w):
            if item not in ids:
                ids.append(item)
        if ids != current['stash_ids']:
            patch['stash_ids'] = ids
        if patch:
            self.stash_update(dict(patch, id=identifier))
        verified = self.stash_detail(identifier)
        if profile.get('images') and 'default=true' in (verified.get('image_path') or ''):
            raise RuntimeError('Provider image was not saved; metadata will retry')
        if not any(x['endpoint'] == STASHDB and x['stash_id'] == w['foreignId'] for x in verified['stash_ids']):
            raise RuntimeError('Enrichment lost provider identity')
        return {'fields_filled': sorted(patch),
                'image': bool(verified.get('image_path')) and 'default=true' not in verified['image_path']}

    def stash_create(self, w):
        # Resolve full metadata BEFORE creation; an outage must not create a stub.
        data = profile_patch(self.profile(w))
        data.update(favorite=True, stash_ids=ids_from_w(w))
        data.setdefault('name', w['name'])
        # Identify may have created this performer during the provider request.
        # Reuse that record rather than relying on the cycle's older inventory.
        matches = [p for p in self.stash_all() if any(
            endpoint(x['endpoint']) == STASHDB and x['stash_id'] == w['foreignId']
            for x in p['stash_ids'])]
        if len(matches) > 1:
            raise RuntimeError('Duplicate Stash identity appeared during metadata retrieval')
        if matches:
            existing = matches[0]
            self.enrich(existing['id'], w)
            return self.stash_update({'id': existing['id'], 'favorite': True})
        return self.gql('''mutation($input:PerformerCreateInput!){performerCreate(input:$input){id favorite}}''',
                        {'input': data})['performerCreate']


def ids_from_w(w):
    ids = [{'endpoint': STASHDB, 'stash_id': w['foreignId']}]
    if w.get('tpdbId'):
        ids.append({'endpoint': TPDB, 'stash_id': w['tpdbId']})
    return ids


def endpoint(value):
    return value.rstrip('/').lower()


class Sync:
    def __init__(self, api, state_path):
        self.api = api
        self.path = Path(state_path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'version': 1, 'pairs': {}}
        if self.state.get('version') != 1:
            raise RuntimeError('Unknown sync state version')

    def run(self, preview=False):
        # Both complete reads must succeed before any changes, so outages aren't deletions.
        ws = self.api.w('performer')
        ss = self.api.stash_all()
        if not isinstance(ws, list):
            raise RuntimeError('Incomplete Whisparr performer listing')
        pairs = self.state['pairs']
        wmap = {w['foreignId']: w for w in ws if w.get('foreignId')}
        tmap = {}
        nmap = {}
        for w in ws:
            if w.get('tpdbId'):
                tmap.setdefault(w['tpdbId'], []).append(w['foreignId'])
            nmap.setdefault(w['name'].strip().casefold(), []).append(w['foreignId'])
        old_ids = {str(p['stash_id']): fid for fid, p in pairs.items() if p.get('stash_id')}
        smap, problems = {}, []
        for s in ss:
            candidates = {x['stash_id'] for x in s['stash_ids'] if endpoint(x['endpoint']) == STASHDB}
            if not candidates:
                for x in s['stash_ids']:
                    if endpoint(x['endpoint']) == TPDB:
                        candidates.update(tmap.get(x['stash_id'], []))
            if not candidates and s['id'] in old_ids:
                candidates.add(old_ids[s['id']])
            # An exact unique local name may link an unlinked record, never fuzzy matching.
            if not candidates and s.get('favorite') and not s['stash_ids'] and not s.get('disambiguation'):
                matches = nmap.get(s['name'].strip().casefold(), [])
                if len(matches) == 1:
                    w = wmap[matches[0]]
                    if not s.get('gender') or s['gender'] == GENDERS.get(w.get('gender')):
                        candidates.add(matches[0])
            if len(candidates) != 1:
                if s.get('favorite'):
                    problems.append({'stash_id': s['id'], 'reason': 'Missing or ambiguous provider identity'})
                continue
            smap.setdefault(next(iter(candidates)), []).append(s)
        actions = []
        keys = set(pairs) | set(smap) | {fid for fid, w in wmap.items() if w.get('monitored')}
        for fid in sorted(keys):
            previous = pairs.get(fid)
            candidates = smap.get(fid, [])
            if len(candidates) > 1:
                problems.append({'foreign_id': fid, 'reason': 'Duplicate Stash performer identity'})
                continue
            s = candidates[0] if candidates else None
            w = wmap.get(fid)
            gender = (s or {}).get('gender') or GENDERS.get((w or {}).get('gender'))
            if gender in self.api.c.get('excluded_genders', set()):
                if (s or {}).get('favorite') or (w or {}).get('monitored'):
                    problems.append({'foreign_id': fid, 'reason': 'Excluded performer gender'})
                continue
            sv, wv = bool(s and s['favorite']), bool(w and w['monitored'])
            if previous is None and not sv and not wv:
                continue
            target = desired(previous, sv, wv)
            try:
                if preview:
                    if target and not w:
                        actions.append({'foreign_id': fid, 'action': 'would_create_whisparr_monitored'})
                    elif w and wv != target:
                        actions.append({'foreign_id': fid, 'action': 'would_set_whisparr_monitored', 'value': target})
                    if target and not s:
                        actions.append({'foreign_id': fid, 'action': 'would_create_stash_favorite'})
                    elif s and sv != target:
                        actions.append({'foreign_id': fid, 'action': 'would_set_stash_favorite', 'value': target})
                    if s and w and not any(endpoint(x['endpoint']) == STASHDB for x in s['stash_ids']):
                        actions.append({'foreign_id': fid, 'action': 'would_link_stashdb_id'})
                    if s and w and target and str(s['id'])+':'+fid not in self.state.get('metadata', {}):
                        actions.append({'foreign_id': fid, 'action': 'would_enrich_stash_profile'})
                    continue
                if target and not w:
                    w = self.api.w('performer', 'POST', {
                        'foreignId': fid, 'fullName': s['name'], 'monitored': True,
                        'moviesMonitored': False, 'whisparrMonitorNewItems': True,
                        'qualityProfileId': self.api.c['quality_profile_id'],
                        'rootFolderPath': self.api.c['root_folder'], 'searchOnAdd': False, 'tags': []})
                    # Some REST implementations return an empty mutation response.
                    w = self.api.w('performer/'+fid)
                    if not w['monitored']:
                        raise RuntimeError('Whisparr add did not persist monitoring')
                    actions.append({'foreign_id': fid, 'action': 'created_whisparr_monitored'})
                elif w and wv != target:
                    fresh = self.api.w('performer/'+str(w['id']))
                    if bool(fresh['monitored']) != wv:
                        raise RuntimeError('Concurrent Whisparr change; retry next cycle')
                    fresh['monitored'] = target
                    self.api.w('performer/'+str(w['id']), 'PUT', fresh)
                    if bool(self.api.w('performer/'+str(w['id']))['monitored']) != target:
                        raise RuntimeError('Whisparr monitoring update did not persist')
                    actions.append({'foreign_id': fid, 'action': 'set_whisparr_monitored', 'value': target})
                if target and not s:
                    made = self.api.stash_create(w)
                    s = {'id': made['id'], 'favorite': made['favorite'], 'stash_ids': ids_from_w(w)}
                    if not s['favorite']:
                        raise RuntimeError('Stash creation did not persist favorite')
                    actions.append({'foreign_id': fid, 'action': 'created_stash_favorite'})
                elif s and sv != target:
                    fresh = self.api.stash_get(s['id'])
                    if fresh is None or bool(fresh['favorite']) != sv:
                        raise RuntimeError('Concurrent Stash change; retry next cycle')
                    updated = self.api.stash_update({'id': s['id'], 'favorite': target})
                    if bool(updated['favorite']) != target:
                        raise RuntimeError('Stash favorite update did not persist')
                    actions.append({'foreign_id': fid, 'action': 'set_stash_favorite', 'value': target})
                if s and w and not any(endpoint(x['endpoint']) == STASHDB for x in s['stash_ids']):
                    self.api.stash_update({'id': s['id'], 'stash_ids': s['stash_ids'] + [ids_from_w(w)[0]]})
                current = {'s': target, 'w': target, 'stash_id': s['id'] if s else None,
                           'whisparr_id': w['id'] if w else None}
                # Checkpoint each successful pair; a crash doesn't discard completed work.
                if current != previous:
                    pairs[fid] = current
                    atomic_json(self.path, self.state)
                # Metadata retry state is separate from choice state: provider outages
                # cannot undo a favorite/unmonitor action already synchronized.
                if s and w and target:
                    metadata = self.state.setdefault('metadata', {})
                    marker = str(s['id'])+':'+fid
                    if marker not in metadata:
                        detail = self.api.enrich(s['id'], w)
                        metadata[marker] = {'version': 1, 'updated_at': time.time(), **detail}
                        atomic_json(self.path, self.state)
                        actions.append({'foreign_id': fid, 'action': 'enriched_stash_profile', **detail})
            except Exception as e:
                problems.append({'foreign_id': fid, 'reason': str(e)[:160]})
        report = {'checked_at': time.time(), 'preview': preview, 'stash_performers': len(ss),
                  'whisparr_monitored': sum(bool(w.get('monitored')) for w in ws),
                  'tracked_pairs': len(pairs), 'actions': actions, 'problems': problems}
        if not preview:
            atomic_json(self.path.parent/'status.json', report)
        return report


def stash_connection(connection):
    scheme = connection.get('Scheme', 'http')
    port = int(connection['Port'])
    if scheme not in ('http', 'https') or not 1 <= port <= 65535:
        raise ValueError('Invalid Stash server connection')
    cookie = connection.get('SessionCookie') or {}
    name, value = cookie.get('Name'), cookie.get('Value')
    if not name or not value or any(c in name+value for c in '\r\n;'):
        raise RuntimeError('Stash did not provide a usable session cookie')
    return {'stash_url': f'{scheme}://127.0.0.1:{port}', 'stash_cookie': f'{name}={value}'}


def plugin_settings(api):
    plugins = api.gql('query{configuration{plugins}}')['configuration']['plugins'] or {}
    if not isinstance(plugins, dict):
        raise RuntimeError('Stash returned invalid plugin settings')
    settings = plugins.get(PLUGIN_ID) or {}
    if not isinstance(settings, dict):
        raise RuntimeError('Stash returned invalid sync settings')
    return settings


def configured_api(connection):
    local = stash_connection(connection)
    api = API(local)
    settings = plugin_settings(api)
    url = str(settings.get('whisparr_url') or '').strip().rstrip('/')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Set a valid Whisparr Eros v3 URL in plugin settings')
    key = str(settings.get('whisparr_api_key') or '').strip()
    if not key:
        raise ValueError('Set the Whisparr API key in plugin settings')
    root = str(settings.get('root_folder') or '').strip()
    if not root:
        raise ValueError('Set the Whisparr root folder in plugin settings')
    try:
        quality = int(settings.get('quality_profile_id'))
    except (TypeError, ValueError):
        raise ValueError('Set a numeric Whisparr quality profile ID') from None
    if quality <= 0:
        raise ValueError('Set a positive Whisparr quality profile ID')
    excluded = {x.strip().upper() for x in str(settings.get('excluded_genders') or '').split(',') if x.strip()}
    if excluded - VALID_GENDERS:
        raise ValueError('Unknown gender in excluded_genders')
    api.c.update(whisparr_url=url, whisparr_api_key=key, root_folder=root,
                 quality_profile_id=quality, excluded_genders=excluded,
                 sync_enabled=settings.get('sync_enabled') is True)
    return api


def validate_defaults(api):
    profiles = api.w('qualityprofile')
    roots = api.w('rootfolder')
    if not isinstance(profiles, list) or not any(p.get('id') == api.c['quality_profile_id'] for p in profiles):
        raise ValueError('Whisparr quality profile ID was not found')
    if not isinstance(roots, list) or not any(r.get('path') == api.c['root_folder'] for r in roots):
        raise ValueError('Whisparr root folder was not found')


def validate(api):
    validate_defaults(api)
    performers = api.w('performer')
    if not isinstance(performers, list):
        raise RuntimeError('Incomplete Whisparr performer listing')
    stash_performers = api.stash_all()
    return {'valid': True, 'whisparr_performers': len(performers),
            'stash_performers': len(stash_performers),
            'excluded_genders': sorted(api.c['excluded_genders'])}


def main():
    os.umask(0o077)
    payload = json.load(sys.stdin)
    connection = payload['server_connection']
    args = payload.get('args') or {}
    mode = args.get('mode', 'sync')
    if mode not in ('sync', 'preview', 'validate', 'hook'):
        raise ValueError('Unknown plugin task mode')
    hook = args.get('hookContext') or {}
    if mode == 'hook':
        kind = hook.get('type')
        if kind == 'Performer.Update.Post' and not ({'favorite', 'stash_ids'} & set(hook.get('inputFields') or [])):
            return {'skipped': 'Unrelated performer update'}
        if kind == 'Performer.Create.Post' and not (hook.get('input') or {}).get('favorite'):
            return {'skipped': 'New performer is not favorited'}
        if kind not in ('Performer.Update.Post', 'Performer.Create.Post', 'Performer.Destroy.Post'):
            return {'skipped': 'Unrelated hook'}
    api = configured_api(connection)
    if mode == 'validate':
        return validate(api)
    if not api.c['sync_enabled'] and mode != 'preview':
        return {'skipped': 'Sync is disabled in plugin settings'}
    validate_defaults(api)
    data_dir = Path(connection['Dir'])/'whisparr-performer-sync'
    state_path = data_dir/'state.json'
    if mode == 'preview':
        return Sync(api, state_path).run(preview=True)
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = data_dir/'sync.lock'
    with lock_path.open('a+b') as lock:
        os.chmod(lock_path, 0o600)
        if not acquire_lock(lock):
            return {'skipped': 'Another sync is running'}
        return Sync(api, state_path).run()


if __name__ == '__main__':
    try:
        result = main()
        if 'actions' in result:
            label = 'preview' if result.get('preview') else 'sync'
            print(f'Whisparr performer {label}: {len(result["actions"])} actions, '
                  f'{len(result["problems"])} problems, {result["tracked_pairs"]} tracked pairs',
                  file=sys.stderr, flush=True)
        print(json.dumps({'output': result}), flush=True)
        sys.exit(1 if result.get('problems') else 0)
    except Exception as e:
        print(json.dumps({'error': str(e)[:160]}), flush=True)
        sys.exit(1)
