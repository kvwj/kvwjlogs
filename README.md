# kvwjlogs

## RDS Override

The Utilities menu includes an RDS Override page that reports the last text sent
by the RDS listener and lets a visitor set an override of up to 64 characters.
An override can last until the next playback update or until manually cleared.
The utility is available to anyone who can access the site.

Apply the Django migrations before deploying the updated web app and RDS
listener. Both processes use the `rds_display_state` table in the shared
PostgreSQL database to exchange the current display text and override.
