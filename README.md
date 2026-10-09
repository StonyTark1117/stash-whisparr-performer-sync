# Stash ↔ Whisparr Performer Sync

A Stash plugin for Whisparr **Eros v3** that keeps Stash performer favorites and Whisparr monitored performers together. This lives on the Stash side, not in Whisparr.

It packages the local sync discussed in [Whisparr-Eros PR #951](https://github.com/Whisparr/Whisparr-Eros/pull/951) as an optional Stash integration, keeping cross-application behavior out of Whisparr itself.

Stash source URL: `https://stonytark1117.github.io/stash-whisparr-performer-sync/index.yml`

## Install

1. Install Python 3 on the Stash host/container. The plugin uses only the standard library. On Windows, make sure `python3` resolves, or edit the first `exec` entry in the plugin YAML to your Python executable.
2. In Stash, open **Settings → Plugins → Available Plugins**, add the source URL above, install **Whisparr Performer Sync**, then reload plugins if prompted. Alternatively, copy the three files from `plugin/` into a `whisparrPerformerSync` folder beneath Stash's plugin directory and reload plugins.
3. In **Settings → Plugins → Installed Plugins**, configure the plugin:

   - Whisparr Eros v3 base URL, reachable **from the Stash host**, e.g. `http://whisparr:6969`.
   - Whisparr API key from Whisparr's General settings. Stash displays this standard STRING setting to administrators; restrict Stash admin access accordingly.
   - Existing Whisparr root folder path and quality profile ID for performers created from Stash favorites.
   - Optional excluded genders: comma-separated `FEMALE`, `MALE`, `TRANSGENDER_FEMALE`, `TRANSGENDER_MALE`, `INTERSEX`, `NON_BINARY`. Leave blank to include all genders (the default).

4. Run **Validate Whisparr sync configuration**, then **Preview Whisparr performer sync** from Stash's Plugin Tasks. Inspect proposed changes before turning on **Enable synchronization**. Preview does not write data or state.
5. Enable synchronization, run **Sync Whisparr monitored performers** once, and inspect Stash's plugin task log and the private `whisparr-performer-sync/status.json` next to Stash's config file.
6. Set up a periodic task for changes made in Whisparr; Stash performer hooks handle relevant Stash-side changes promptly. See below.

The StashDB stash-box source must be configured in Stash for profile enrichment. If it is unavailable, the plugin defers creating a Stash performer rather than creating a bare record. Existing favorites/monitoring still sync; metadata failures are retried separately.

## Scheduling Whisparr → Stash polling

Whisparr has no Stash hook, so schedule the bundled `trigger_sync.py` on the Stash host. It queues the Stash plugin task through GraphQL; the API key is read from a private file, never supplied on the command line.

On Linux, create a mode-`0600` file containing your **Stash** API key, owned by the Stash service user. Adapt the paths and URL in `deploy/stash-whisparr-sync.service`, install that unit and `deploy/stash-whisparr-sync.timer` under `/etc/systemd/system/`, then run `systemctl daemon-reload && systemctl enable --now stash-whisparr-sync.timer`. The example assumes a plugin source local path of `whisparr-performer-sync-source`; Stash's installed path may differ, so locate its `trigger_sync.py`. The example polls one minute after each previous invocation. The plugin uses a process lock so overlapping runs do not compete.

For a normal cron installation, use a one-minute entry (adjust paths and URL):

```cron
* * * * * /usr/bin/python3 /path/to/stash/plugins/whisparrPerformerSync/trigger_sync.py --stash-url http://127.0.0.1:9999 --api-key-file /private/path/stash-api-key
```

For Docker, run the scheduler where the Stash URL, plugin script, and key file are accessible (inside the container or in a sidecar). A host cron entry can invoke the script in a container named `stash` when the key is mounted inside it:

```cron
* * * * * docker exec stash python3 /root/.stash/plugins/whisparrPerformerSync/trigger_sync.py --stash-url http://127.0.0.1:9999 --api-key-file /run/secrets/stash-api-key
```

For Windows Task Scheduler, create a task that repeats every **1 minute indefinitely**. Set **Program/script** to your Python executable (for example `C:\Python313\python.exe`) and **Add arguments** to:

```text
"C:\Stash\plugins\whisparrPerformerSync\trigger_sync.py" --stash-url http://127.0.0.1:9999 --api-key-file "C:\Stash\private\stash-api-key"
```

Run the task as the same user that owns the Stash configuration and restrict the key file with Windows file permissions. For Stash itself, edit the plugin YAML's `exec` entry from `python3` to your Python executable if needed. The plugin uses an OS-level lock on both Unix and Windows.

## How conflicts and identities work

- On first sync, a favorite **or** monitored performer enables both sides. Thereafter the side changed since the last successful pair checkpoint wins. If both sides change incompatibly at once, removal wins.
- Deleting a previously paired Stash performer unmonitors the Whisparr performer; deleting its Whisparr counterpart unfavorites the Stash performer. Neither deletion is automatically recreated. Both complete inventories must load before any mutation, preventing a temporary outage from looking like a deletion.
- Matching prefers exact StashDB IDs, then ThePornDB IDs, then a previously paired Stash ID. A unique exact name-and-gender match is used only for an unlinked favorite without disambiguation. Ambiguous identities are reported, never guessed.
- Creating a Whisparr performer uses the configured root folder and quality profile, keeps `searchOnAdd` off, and does not change other monitoring preferences. Creating a Stash performer fetches its exact StashDB profile first, including available artwork/metadata. Enrichment fills empty fields and merges lists without overwriting manual edits.
- State and status are stored under `<Stash config directory>/whisparr-performer-sync/`, outside the plugin install directory so plugin updates do not erase them. The plugin creates them with private permissions. Back this directory up along with Stash.

## Migrating the older standalone sync

Stop the old timer before enabling this plugin. Copy its `state.json` (v1) into `<Stash config directory>/whisparr-performer-sync/state.json`, owned by the Stash user with mode `0600`. Configure `excluded_genders` to `MALE, TRANSGENDER_MALE` if you used the original local defaults. Run Validate and Preview. With the existing pair state intact, an unchanged installation should propose no favorite/monitoring changes; metadata retries may still appear. Enable the plugin, run Sync once, verify results, then enable the new scheduler. Retain the old state backup for rollback.

## Development

```sh
PYTHONPATH=plugin python3 -m unittest discover -s tests -v
python3 scripts/build_site.py
```

The latter creates a plugin zip and SHA-256-verified Stash source index under `dist/site`. GitHub Actions tests and publishes that directory to GitHub Pages.

License: MIT.
