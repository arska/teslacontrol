FROM dockerhub.vshn.net/library/php:8.4-apache

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libcurl4-gnutls-dev \
    gettext-base \
    && rm -rf /var/lib/apt/lists/*

# Install PHP extensions
RUN docker-php-ext-install mysqli gettext
RUN pecl install redis && docker-php-ext-enable redis

# Enable Apache modules
RUN a2enmod rewrite

# Configure Apache for port 8080 (OpenShift non-root requirement)
RUN sed -i 's/Listen 80/Listen 8080/' /etc/apache2/ports.conf && \
    sed -i 's/:80/:8080/g' /etc/apache2/sites-available/000-default.conf

# Configure Apache to allow .htaccess overrides (emoncms uses them)
RUN sed -i '/<Directory \/var\/www\/>/,/<\/Directory>/ s/AllowOverride None/AllowOverride All/' /etc/apache2/apache2.conf

# Clone emoncms and modules
ARG EMONCMS_VERSION=11.15.15
RUN git clone --depth 1 --branch ${EMONCMS_VERSION} https://github.com/emoncms/emoncms.git /var/www/html && \
    git clone --depth 1 https://github.com/emoncms/graph.git /var/www/html/Modules/graph && \
    git clone --depth 1 https://github.com/emoncms/dashboard.git /var/www/html/Modules/dashboard && \
    git clone --depth 1 https://github.com/emoncms/app.git /var/www/html/Modules/app

# Create data and log directories
RUN mkdir -p /var/opt/emoncms/phpfina /var/opt/emoncms/phptimeseries /var/log/emoncms

# Copy entrypoint
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Fix permissions for OpenShift arbitrary UID (runs as random UID in GID 0)
RUN chgrp -R 0 /var/www/html /var/opt/emoncms /var/log/emoncms \
    /var/run/apache2 /var/lock/apache2 /tmp \
    /usr/local/etc/php && \
    chmod -R g=u /var/www/html /var/opt/emoncms /var/log/emoncms \
    /var/run/apache2 /var/lock/apache2 /tmp \
    /usr/local/etc/php

# Remove PID file location issues
RUN sed -i 's|${APACHE_PID_FILE}|/tmp/apache2.pid|' /etc/apache2/envvars || true
ENV APACHE_PID_FILE=/tmp/apache2.pid

EXPOSE 8080

ENTRYPOINT ["entrypoint.sh"]
CMD ["apache2-foreground"]
