FROM php:8.4-cli
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev \
    && docker-php-ext-install pdo_pgsql \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY index.php .
USER www-data
CMD ["php", "-S", "0.0.0.0:8077", "index.php"]
