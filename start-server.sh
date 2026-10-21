mkdir /mnt/announcements
mkdir /mnt/announcements/media
mount -t cifs //${SERVER}/${FOLDER}/Song\ Announcements -o user=${USER},pass=${PASS},uid=33,gid=33,dir_mode=0755,file_mode=0755 /mnt/announcements/media
cd kvwjlogs; gunicorn kvwjlogs.wsgi --user www-data --bind 0.0.0.0:8888 &
nginx -g "daemon off;"
