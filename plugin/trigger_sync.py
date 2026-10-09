#!/usr/bin/env python3
"""Queue one sync task through Stash GraphQL (for cron/systemd schedulers)."""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stash-url', required=True)
    parser.add_argument('--api-key-file', type=Path, required=True)
    args = parser.parse_args()
    key = args.api_key_file.read_text().strip()
    if not key:
        raise ValueError('Stash API key file is empty')
    query = '''mutation($plugin:ID!,$task:String!){
      runPluginTask(plugin_id:$plugin,task_name:$task)
    }'''
    request = urllib.request.Request(
        args.stash_url.rstrip('/')+'/graphql',
        data=json.dumps({'query': query, 'variables': {
            'plugin': 'whisparrPerformerSync',
            'task': 'Sync Whisparr monitored performers'}}).encode(),
        headers={'ApiKey': key, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'Stash HTTP {error.code}') from None
    if result.get('errors') or not result.get('data', {}).get('runPluginTask'):
        raise RuntimeError('Stash did not queue the sync task')
    print('Queued Whisparr performer sync in Stash')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
