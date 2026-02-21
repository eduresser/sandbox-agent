FROM rocker/r-ver:4

# Prevent OpenBLAS from spawning dozens of threads inside a PID-limited
# container. Without this, any R process that loads a .so triggers
# "pthread_create failed" and "failed to map segment from shared object".
ENV OPENBLAS_NUM_THREADS=1

# rocker/r-ver:4 already configures PPM for the correct Ubuntu release
# (Noble), so install.packages() gets fast pre-compiled binaries that
# match the system libraries.

COPY kernel/client.r.c /tmp/client.r.c

# Compile the C client and install base R packages, then strip build
# tools to keep the image lean. Additional system libraries can be
# installed at runtime via execute_terminal when TERMINAL_ROOT=true.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && mkdir -p /kernel \
    && gcc -O2 -static -o /kernel/client_r /tmp/client.r.c \
    && rm /tmp/client.r.c \
    && Rscript -e "install.packages(c('jsonlite', 'base64enc'), Ncpus=4)" \
    && apt-mark manual make libgomp1 \
    && apt-get purge -y --auto-remove gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 65532 sandbox \
    && useradd -u 65532 -g 65532 -d /home/sandbox -m sandbox \
    && mkdir -p /workspace /usr/local/lib/R/user-library \
    && chown sandbox:sandbox /workspace /usr/local/lib/R/user-library

COPY kernel/kernel_r.R /kernel/kernel_r.R

# R packages live on the rootfs (not tmpfs) so shared objects can be loaded.
# Docker mounts /home/sandbox as tmpfs with noexec, which breaks dyn.load().
ENV R_LIBS_USER=/usr/local/lib/R/user-library

WORKDIR /workspace
USER sandbox

CMD ["Rscript", "/kernel/kernel_r.R"]
