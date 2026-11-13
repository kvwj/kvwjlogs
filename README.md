# kvwjlogs

## Category Announcements

Utilities > Announcements generates audio in the existing Celery worker
for any category except Sunday. Select any of the four announcement variants and choose to
generate missing files or regenerate all selected files. Variants 3 and 4 need a
track year; these are skipped when the year is absent. Progress includes generated,
skipped, and failed counts with individual errors. No schema migration is required.
The worker needs the OpenAI API credentials and the existing announcement media
mount. Generation uses the same voice, model, and filenames as the existing audio
tools. Like the other utilities, this page is available without a login.
The playback log and track tables (including artist pages) offer playback
controls for all four announcement variants.

## RDS Override

The Utilities menu includes an RDS Override page that reports the last text sent
by the RDS listener and lets a visitor set an override of up to 64 characters.
An override can last until the next playback update or until manually cleared.
The utility is available to anyone who can access the site.

Apply the Django migrations before deploying the updated web app and RDS
listener. Both processes use the `rds_display_state` table in the shared
PostgreSQL database to exchange the current display text and override.

## Audio generation dependencies

Celery's Redis transport requires the Python `packaging` library, which is
included in `requirements.txt`. Rebuild and redeploy the web app image after
changing requirements so both the web process and Celery worker receive the
updated dependencies.
