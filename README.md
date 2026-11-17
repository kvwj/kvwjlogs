# KVWJ Logs

KVWJ Logs is a Django application for browsing the station's playback history,
tracks, artists, and scan log. It also provides tools for generating track
announcement audio and setting the text shown by the RDS encoder.

## Features

- **Playback Log** (`/`): browse and filter playback events by category, artist,
  track, year, and date. The table includes audio controls for available
  announcements.
- **Tracks** (`/tracks`): browse and filter the track catalog, edit display
  names, and play available announcement files.
- **Artists** (`/artists`): browse and filter artists, edit display names, and
  generate missing announcement files or regenerate all selected files for an
  artist. Regenerating overwrites existing selected recordings; missing-only
  generation leaves them unchanged. Both modes use the selected shift and
  varieties and skip year-based varieties when a track has no year. The 80s
  show option appears only for artists with a track in an `80s` category.
- **Scan Log** (`/log/`): browse the scan log.
- **Utilities > Announcements**: choose a category (except Sunday), select any
  combination of four announcement varieties for one voice set and a matching
  speech service/voice, then
  generate missing files or regenerate the selected files. Progress reports
  generated, skipped, and failed files. Year-based variants are skipped for
  tracks without a year. A preview generates a separate sample using a random
  track with a title and artist without altering saved announcements.
- **Utilities > RDS Override**: view the last text reported by the RDS listener
  and set an override for up to 64 characters. Choose to clear it manually or
  have it expire on the next playback update.
- **Utilities > Settings**: choose a voice for each shift for track and artist
  regeneration. The shared defaults are Microsoft Nova DragonHD, Andrew
  DragonHD, and Davis DragonHD. Set daily shift start times in the
  `America/Denver` time zone, plus the Saturday 80s show start and end times.
  The show defaults to 9:00 PM through midnight and uses Microsoft Emma
  DragonHD for tracks in categories containing `80s`.

Announcement text variants are:

1. `{artist} with {title} on J-95!`
2. `{title}, that's {artist} on K-V-W-J!`
3. `{year} music from {artist} on K-V-W-J!`
4. `That was {title} from {year} on J-95!`

Each variety has four recordings. Shifts 1, 2, and 3 use file variants 1–4,
5–8, and 9–12, and the 80s show uses 13–16, in the same order. The playback
and track tables, including the track table on artist pages, provide a compact
selector and Play control for all sixteen files. Files are served from `/media/`
using names in the form `<track-id>-<variant>~0.mp3`.
The dropdown labels are Morning (Nova), Afternoon (Andrew), and Night (Davis).
The 80s show (Emma) group appears only for tracks whose category contains
`80s` (case-insensitive).
The track edit page shows whether each file exists and its recorded service and
voice, or indicates when that information is unknown or unavailable.

## Architecture

- **Django** serves the web application and reads/writes PostgreSQL.
- **Celery** runs audio generation as background tasks. The web app queues tasks
  through Redis; Redis database 0 is the broker and database 1 is the result
  backend. The Celery worker is started by `start-server.sh`.
- **Audio generation** supports OpenAI's `gpt-4o-mini-tts` model with Nova or
  Ash and the instruction to speak with excitement like a radio DJ, or Microsoft
  Speech Service DragonHD voices Nova, Ava, Emma, Andrew, Brian, or Davis.
- **Announcement media** is mounted at `/mnt/announcements/media`. Nginx serves
  this directory under `/media/`; the Celery worker must be able to write to the
  same mount.
- **RDS display state** is shared with the separate `rds-scripts` listener using
  the PostgreSQL table `rds_display_state`. Deploy a listener version that
  reads this table for overrides and records the last text sent.

The Redis instance is also used for Celery progress and task results. RDS
overrides are stored in PostgreSQL so they do not depend on Redis persistence.

## Database

The application expects the existing PostgreSQL tables `playback`, `tracks`,
`artists`, `categories`, `years`, and `log`. These tables were created outside
Django and their models are marked `managed = False`; Django migrations do not
create, alter, or delete them.

The `RdsDisplayState`, `AnnouncementVoice`, and `UtilitySettings` models are
managed by Django. Their migrations create `rds_display_state`,
`announcement_voice`, and `utility_settings`. For an existing deployment,
apply migrations after setting the production database environment:

```bash
python3 kvwjlogs/manage.py migrate logviewer
```

To inspect pending operations or SQL before applying:

```bash
python3 kvwjlogs/manage.py migrate logviewer --plan
python3 kvwjlogs/manage.py sqlmigrate logviewer 0002
```

Back up the production database before applying schema changes. The first
migration's existing-table model entries are unmanaged; the RDS migration
creates `rds_display_state`. Migrations 0003 and 0004 create
`announcement_voice` and `utility_settings`, respectively. Migration 0005
changes any saved legacy Microsoft track voice setting to DragonHD Nova;
OpenAI settings and existing announcement voice records are unchanged.
Migration 0006 adds separate male regeneration settings, defaulting to Microsoft
Andrew DragonHD; it does not change the existing female setting. Migration 0007
adds shift start times (5:00 AM and 5:00 PM by default) and ensures the shared
settings row exists. Migration 0008 updates the female voice defaults, and 0009
changes an existing OpenAI/Nova female setting to Microsoft Nova DragonHD.
Migration 0010 adds the third voice and replaces the existing shift schedule
with 6:00 AM, noon, and 7:00 PM. Other saved voice choices, existing
announcement files, and voice records are unchanged. Migration 0011 adds the
Saturday show hours without changing the existing daily schedule.

## Configuration

The Django settings read these environment variables:

| Variable | Purpose |
| --- | --- |
| `DATABASE_NAME` | PostgreSQL database name |
| `DATABASE_USER` | PostgreSQL user |
| `DATABASE_PASSWORD` | PostgreSQL password |
| `DATABASE_HOST` | PostgreSQL hostname (defaults to `db`) |
| `DATABASE_PORT` | PostgreSQL port (defaults to `5432`) |
| `DJANGO_SECRET_KEY` | Django secret key |

Other runtime configuration:

| Variable / setting | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Credentials used by the OpenAI client for speech generation |
| `AZURE_SPEECH_KEY` | Azure Speech resource key used for Microsoft Speech Service |
| `AZURE_SPEECH_REGION` | Azure Speech resource region; required for Microsoft generation |
| `CELERY_BROKER_URL` | Redis broker URL; currently `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | Redis task result URL; currently `redis://redis:6379/1` |
| `SERVER`, `FOLDER`, `USER`, `PASS` | SMB share settings used by the container startup script to mount announcement media |

The Redis hostname `redis` must resolve from the web/Celery container. The
announcement mount and credentials for the selected speech service must be
available to the Celery worker, not just the web process.

The separate `rds-scripts` listener uses its own `DB_HOST`, `DB_NAME`,
`DB_USER`, and `DB_PASSWORD` variables for the shared PostgreSQL database.
Configure both applications to connect to the same database.

## Local development

Create and activate a virtual environment, then install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Set the PostgreSQL environment variables above and make sure they point to the
intended development database. Management commands change the database selected
by those variables. Then, from the repository root:

```bash
python3 kvwjlogs/manage.py migrate
python3 kvwjlogs/manage.py runserver
```

For background audio generation, Redis must be reachable at the configured
broker/result URLs, the announcement directory must be mounted and writable,
and credentials for the selected speech service must be available. Start a
Celery worker from the Django project directory:

```bash
cd kvwjlogs
python -m celery -A kvwjlogs.celery:app worker --loglevel=INFO
```

## Docker deployment

The root `Dockerfile` builds the web image. It installs the Python dependencies,
Nginx, and SMB utilities. `start-server.sh` mounts the configured SMB
announcement share, then starts the Celery worker, Gunicorn, and Nginx.

Build from the repository root:

```bash
docker build -t kvwjlogs .
```

Provide the database, SMB, speech service, and network configuration required by
the container. The database and Redis services must be reachable from the
application container, and the SMB mount must contain the announcement media.
Rebuild and redeploy after changing `requirements.txt` or application code.

## Audio generation

Category generation is asynchronous. The web request queues a Celery task; the
browser polls task status and displays progress and any per-file errors.

- Choose **OpenAI** with **Nova** (the default) or **Ash**, or **Microsoft Speech
  Service** with DragonHD **Nova**, **Ava**, **Emma**, **Andrew**, **Brian**, or
  **Davis**.
  Voice choices update when the service changes.
  Select variants from one shift and a matching voice (female for shift 1,
  male for shifts 2 and 3). The 80s show set requires Microsoft Emma DragonHD.
  Generate each set in a separate category job. This selection applies only to
  that job.
  Artist-wide and single-track regeneration use the corresponding shared
  Utilities > Settings voice, captured when the job is queued.
- Microsoft uses DragonHD voices through Azure's regional Speech REST endpoint,
  producing 48 kHz, 192 kbit/s mono MP3 files. Configure `AZURE_SPEECH_KEY` and
  the matching `AZURE_SPEECH_REGION` in the worker environment and
  restart/redeploy it. Standalone HD voices require a supported region such as
  `westus2`; `westus3` is not listed as supporting standalone HD voices in
  [Microsoft's region table](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions?tabs=tts).
  Keys are region-specific. No Azure OpenAI model deployment is needed.
- Microsoft requests use an SSML alias to say the final `J-95` station
  sign-off as "Jay ninety five"; OpenAI requests keep the original text.
- DragonHD generation and previews use the original track and artist display
  names. Previously queued jobs using legacy Microsoft voices retain their
  former sentence-cased track titles.
- Provider changes are manual. An OpenAI failure (including exhausted credits)
  is reported rather than automatically retrying with Microsoft.
- **Generate voice preview** uses a random track with a title and artist shown
  before generation. The track stays selected for comparing voices until
  **Choose another track** is pressed. Each fresh sample may incur charges.
  Preview audio is stored temporarily in the Celery result backend and never
  replaces the saved MP3.
- **Generate missing files** leaves existing selected announcement files in
  place, regardless of which service or voice created them. File names do not
  change with the provider or voice.
- **Regenerate selected files** overwrites the selected files and can incur
  charges from the selected speech service.
- Variants 3, 4, 7, 8, 11, 12, 15, and 16 need a year. They are
  skipped when the track has no year.
- The Announcements category dropdown omits Sunday, but does not delete or alter
  that database category.

The standalone script `generate_files_by_category.py` in the separate
`openai-speech` repository is the source of the category announcement wording.

The app records the provider and voice for each saved track/variant in
`announcement_voice`, including artist-wide and single-track regeneration. A
blank provider and voice mean that the metadata is unknown, for example if a
file replacement succeeded but saving its metadata failed. Editing or generating
files outside this app does not update the table. Older Microsoft recordings
retain their original Turbo or standard voice labels rather than being
identified as DragonHD.

To initialize metadata for existing files, after applying migrations run this
once in the deployed environment with the announcement share mounted and the
production database configured:

```bash
python3 kvwjlogs/manage.py backfill_announcement_voices --confirm-openai-nova
```

Only run it if the existing untracked female files were generated with
OpenAI/Nova. It checks variants 1–4 for each database track and never overwrites
an existing metadata record, including unknown ones; it does not backfill male
variants 5–8. Confirm the share is mounted and check the reported file count;
running against an empty directory will not initialize the missing records.

The separate `back-announcements` listener reads the shift start times from
Utilities > Settings on each playback update, in `America/Denver`. Defaults are
shift 1 at 6:00 AM, shift 2 at noon, and shift 3 at 7:00 PM. During the
Saturday 80s show, tracks in categories containing `80s` (case-insensitive)
use the Emma set; other tracks continue using the regular shift. Show end
times earlier than the start mean Sunday; equal times are not allowed.

To deploy, apply migrations through 0011, deploy the updated web app and
worker, generate shift 3 and 80s show files for the appropriate categories,
then deploy the updated listener. Earlier listeners do not know the new sets;
migration 0010 also changes the daily times they read. When the active set has
no file for a track, the updated listener uses the dummy, not another shift.
If it cannot read valid shift times, it logs an error and uses the dummy.

## RDS override

The RDS utility and the separate listener exchange state through
`rds_display_state`. Apply the Django migration before deploying a listener
version that queries this table. The status shown is the last text the listener
reported sending; it is not a direct read from the encoder hardware.

## Security notes

The Utilities pages, including RDS override, settings, and category audio
generation, do not currently require a login. Anyone who can reach the site can
change the RDS text, alter the shared speech settings, or queue audio generation
that may incur API charges. Restrict access at the network or proxy layer unless
that is intentional.

Before exposing this application publicly, review Django's deployment security
settings. In particular, configure `DEBUG`, `ALLOWED_HOSTS`, and
`CSRF_TRUSTED_ORIGINS` for the deployment instead of relying on development
defaults.

## Tests and checks

Run Django checks from the repository root:

```bash
python3 kvwjlogs/manage.py check
```

Run the application tests:

```bash
python3 kvwjlogs/manage.py test logviewer
```

The tests need a reachable database using the configured settings, or a test
settings module that provides an isolated database.
