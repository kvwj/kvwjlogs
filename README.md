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
  regenerate an artist's first two announcement files.
- **Scan Log** (`/log/`): browse the scan log.
- **Utilities > Announcements**: choose a category (except Sunday), select any
  combination of four announcement variants, then generate missing files or
  regenerate the selected files. Progress reports generated, skipped, and
  failed files. Year-based variants are skipped for tracks without a year.
- **Utilities > RDS Override**: view the last text reported by the RDS listener
  and set an override for up to 64 characters. Choose to clear it manually or
  have it expire on the next playback update.

Announcement text variants are:

1. `{artist} with {title} on J-95!`
2. `{title}, that's {artist} on K-V-W-J!`
3. `{year} music from {artist} on K-V-W-J!`
4. `That was {title} from {year} on J-95!`

The playback and track tables, including the track table on artist pages, have
Play controls for all four variants. Files are served from `/media/` using names
in the form `<track-id>-<variant>~0.mp3`.

## Architecture

- **Django** serves the web application and reads/writes PostgreSQL.
- **Celery** runs audio generation as background tasks. The web app queues tasks
  through Redis; Redis database 0 is the broker and database 1 is the result
  backend. The Celery worker is started by `start-server.sh`.
- **OpenAI audio generation** uses the `gpt-4o-mini-tts` model, the `nova` voice,
  and the instruction to speak with excitement like a radio DJ.
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

The `RdsDisplayState` model is managed by Django. Its migration creates the
`rds_display_state` table used by the web app and RDS listener. For an existing
deployment, apply the migration after setting the production database
environment:

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
creates `rds_display_state`.

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
| `CELERY_BROKER_URL` | Redis broker URL; currently `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | Redis task result URL; currently `redis://redis:6379/1` |
| `SERVER`, `FOLDER`, `USER`, `PASS` | SMB share settings used by the container startup script to mount announcement media |

The Redis hostname `redis` must resolve from the web/Celery container. The
announcement mount and OpenAI credentials must be available to the Celery
worker, not just the web process.

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
and `OPENAI_API_KEY` must be available. Start a Celery worker from the Django
project directory:

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

Provide the database, SMB, OpenAI, and network configuration required by the
container. The database and Redis services must be reachable from the
application container, and the SMB mount must contain the announcement media.
Rebuild and redeploy after changing `requirements.txt` or application code.

## Audio generation

Category generation is asynchronous. The web request queues a Celery task; the
browser polls task status and displays progress and any per-file errors.

- **Generate missing files** leaves existing selected announcement files in
  place.
- **Regenerate selected files** overwrites the selected files and can incur
  OpenAI API charges.
- Variants 3 and 4 need a year. They are skipped when the track has no year.
- The Announcements category dropdown omits Sunday, but does not delete or alter
  that database category.

The standalone script `generate_files_by_category.py` in the separate
`openai-speech` repository is the source of the category announcement wording.

## RDS override

The RDS utility and the separate listener exchange state through
`rds_display_state`. Apply the Django migration before deploying a listener
version that queries this table. The status shown is the last text the listener
reported sending; it is not a direct read from the encoder hardware.

## Security notes

The Utilities pages, including RDS override and category audio generation, do
not currently require a login. Anyone who can reach the site can change the RDS
text or queue audio generation that may incur API charges. Restrict access at
the network or proxy layer unless that is intentional.

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
