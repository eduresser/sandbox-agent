FROM r-base:latest

# Prevent OpenBLAS from spawning dozens of threads inside a PID-limited
# container. Without this, any R process that loads a .so triggers
# "pthread_create failed" and "failed to map segment from shared object".
ENV OPENBLAS_NUM_THREADS=1

# Posit Package Manager serves pre-compiled binaries for Linux, making
# install.packages() ~5-10x faster than compiling from CRAN source.
ENV PPM_REPO=https://packagemanager.posit.co/cran/__linux__/bookworm/latest

# Global R profile so ALL R processes (kernel, install_cmd, user terminal)
# use PPM binaries automatically.
RUN printf '\
options(repos = c(CRAN = Sys.getenv("PPM_REPO",\n\
  "https://packagemanager.posit.co/cran/__linux__/bookworm/latest")))\n\
options(HTTPUserAgent = sprintf(\n\
  "R/%%s R (%%s)", getRversion(),\n\
  paste(getRversion(), R.version["platform"], R.version["arch"], R.version["os"])))\n\
' > /etc/R/Rprofile.site

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && Rscript -e "install.packages(c('jsonlite', 'base64enc'), Ncpus=4)" \
    && groupadd -g 65532 sandbox \
    && useradd -u 65532 -g 65532 -d /home/sandbox -m sandbox \
    && mkdir -p /workspace /usr/local/lib/R/user-library \
    && chown sandbox:sandbox /workspace /usr/local/lib/R/user-library

COPY kernel/kernel_r.R /kernel/kernel_r.R
COPY kernel/client_r.py /kernel/client_r.py

# R packages live on the rootfs (not tmpfs) so shared objects can be loaded.
# Docker mounts /home/sandbox as tmpfs with noexec, which breaks dyn.load().
ENV R_LIBS_USER=/usr/local/lib/R/user-library

WORKDIR /workspace
USER sandbox

CMD ["Rscript", "/kernel/kernel_r.R"]
