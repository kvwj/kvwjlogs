# Dockerfile

FROM python:3.14-slim

# install nginx
RUN apt-get update && apt-get install nginx vim -y --no-install-recommends && apt-get install libxml2-dev libxmlsec1-dev libxmlsec1-openssl xmlsec1 pkg-config cifs-utils -y
COPY nginx.default /etc/nginx/sites-available/default
RUN ln -sf /dev/stdout /var/log/nginx/access.log \
    && ln -sf /dev/stderr /var/log/nginx/error.log

# copy source and install dependencies
RUN mkdir -p /opt/app
RUN mkdir -p /opt/app/kvwjlogs
COPY requirements.txt start-server.sh /opt/app/
COPY kvwjlogs /opt/app/kvwjlogs/
WORKDIR /opt/app
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --upgrade setuptools
RUN chown -R www-data:www-data /opt/app

# start server
EXPOSE 80
STOPSIGNAL SIGTERM
CMD /opt/app/start-server.sh
