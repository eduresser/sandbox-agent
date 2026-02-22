FROM rocker/r-ver:4

# Prevent OpenBLAS from spawning dozens of threads inside a PID-limited
# container. Without this, any R process that loads a .so triggers
# "pthread_create failed" and "failed to map segment from shared object".
ENV OPENBLAS_NUM_THREADS=1

# rocker/r-ver:4 already configures PPM for the correct Ubuntu release
# (Noble), so install.packages() gets fast pre-compiled binaries that
# match the system libraries.

COPY client/client_c.c /tmp/client_c.c

# 1. Install system libraries needed by popular R packages:
#    - libxml2-dev      → xml2 (tidyverse dep)
#    - libcurl4-openssl-dev → httr2, curl
#    - libssl-dev        → openssl, httr2
#    - libfontconfig1-dev, libfreetype6-dev, libharfbuzz-dev,
#      libfribidi-dev    → textshaping, ragg (ggplot2 text rendering)
#    - libpng-dev, libtiff-dev, libjpeg-dev → image output devices
#    - libcairo2-dev     → cairo graphics device (ggplot2)
#    - libgit2-dev       → gert/git2r (devtools)
#    - libsqlite3-dev    → RSQLite
#    - libpq-dev         → RPostgres
# 2. Compile the unified C client.
# 3. Install kernel deps + most popular R packages (by CRAN downloads).
#    PPM provides pre-compiled binaries so this is fast.
#
# Packages chosen by CRAN download rank (rpkg.net) and breadth:
#   Tidyverse:  tidyverse (dplyr, ggplot2, tidyr, readr, purrr, tibble,
#               stringr, forcats + deps like rlang, cli, vctrs, scales)
#   Data:       data.table, readxl, haven
#   Web/API:    httr2
#   Database:   DBI, RSQLite
#   Reporting:  rmarkdown, knitr
#   Dev tools:  devtools
#   Stats/ML:   glmnet, randomForest
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc libc6-dev \
       libxml2-dev libcurl4-openssl-dev libssl-dev \
       libfontconfig1-dev libfreetype6-dev libharfbuzz-dev libfribidi-dev \
       libpng-dev libtiff-dev libjpeg-dev libcairo2-dev \
       libgit2-dev libsqlite3-dev libpq-dev \
       pandoc \
    && mkdir -p /kernel /client \
    && gcc -O2 -static -o /client/client_c /tmp/client_c.c \
    && rm /tmp/client_c.c \
    && Rscript -e "install.packages(c( \
         'jsonlite', 'base64enc', \
         'tidyverse', 'data.table', 'readxl', 'haven', \
         'httr2', \
         'DBI', 'RSQLite', \
         'rmarkdown', 'knitr', \
         'devtools', \
         'glmnet', 'randomForest' \
       ), Ncpus=4)" \
    && apt-mark manual make libgomp1 \
       libxml2 libcurl4t64 libssl3t64 \
       libfontconfig1 libfreetype6 libharfbuzz0b libfribidi0 \
       libpng16-16t64 libtiff6 libjpeg-turbo8 libcairo2 \
       libgit2-1.7 libsqlite3-0 libpq5 \
       pandoc \
    && apt-get purge -y --auto-remove gcc libc6-dev \
       libxml2-dev libcurl4-openssl-dev libssl-dev \
       libfontconfig1-dev libfreetype6-dev libharfbuzz-dev libfribidi-dev \
       libpng-dev libtiff-dev libjpeg-dev libcairo2-dev \
       libgit2-dev libsqlite3-dev libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 65532 sandbox \
    && useradd -u 65532 -g 65532 -d /home/sandbox -m sandbox \
    && mkdir -p /workspace /usr/local/lib/R/user-library \
    && chown sandbox:sandbox /workspace /usr/local/lib/R/user-library

COPY kernel/kernel_r.R /kernel/kernel_r.R

ENV KERNEL_PORT=8765

# R packages live on the rootfs (not tmpfs) so shared objects can be loaded.
# Docker mounts /home/sandbox as tmpfs with noexec, which breaks dyn.load().
ENV R_LIBS_USER=/usr/local/lib/R/user-library

WORKDIR /workspace
USER sandbox

CMD ["Rscript", "/kernel/kernel_r.R"]
