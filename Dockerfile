# INTERCEPT - Signal Intelligence Platform
# Docker container for running the web interface
# Multi-stage build: builder compiles tools, runtime keeps only what's needed

###############################################################################
# Stage 1: Builder — compile all tools from source
###############################################################################
FROM python:3.11-slim AS builder

WORKDIR /tmp/build

# Install ALL build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    pkg-config \
    cmake \
    librtlsdr-dev \
    libusb-1.0-0-dev \
    libncurses-dev \
    libsndfile1-dev \
    libgtk-3-dev \
    libasound2-dev \
    libsoapysdr-dev \
    libhackrf-dev \
    liblimesuite-dev \
    libfftw3-dev \
    libpng-dev \
    libtiff-dev \
    libjemalloc-dev \
    libvolk-dev \
    libnng-dev \
    libzstd-dev \
    libsqlite3-dev \
    libcurl4-openssl-dev \
    zlib1g-dev \
    libzmq3-dev \
    libpulse-dev \
    libfftw3-bin \
    liblapack-dev \
    libglib2.0-dev \
    libxml2-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create staging directory for all built artifacts
RUN mkdir -p /staging/usr/bin /staging/usr/local/bin /staging/usr/local/lib /staging/opt

# Build dump1090
RUN cd /tmp \
    && git clone --depth 1 https://github.com/flightaware/dump1090.git \
    && cd dump1090 \
    && sed -i 's/-Werror//g' Makefile \
    && make BLADERF=no RTLSDR=yes \
    && cp dump1090 /staging/usr/bin/dump1090-fa \
    && ln -s /usr/bin/dump1090-fa /staging/usr/bin/dump1090 \
    && rm -rf /tmp/dump1090

# Build AIS-catcher
RUN cd /tmp \
    && git clone https://github.com/jvde-github/AIS-catcher.git \
    && cd AIS-catcher \
    && mkdir build && cd build \
    && cmake .. \
    && make \
    && cp AIS-catcher /staging/usr/bin/AIS-catcher \
    && rm -rf /tmp/AIS-catcher

# Build readsb
RUN cd /tmp \
    && git clone --depth 1 https://github.com/wiedehopf/readsb.git \
    && cd readsb \
    && make BLADERF=no PLUTOSDR=no SOAPYSDR=yes \
    && cp readsb /staging/usr/bin/readsb \
    && rm -rf /tmp/readsb

# Build rx_tools
RUN cd /tmp \
    && git clone https://github.com/rxseger/rx_tools.git \
    && cd rx_tools \
    && mkdir build && cd build \
    && cmake .. \
    && make \
    && DESTDIR=/staging make install \
    && rm -rf /tmp/rx_tools

# Build acarsdec
RUN cd /tmp \
    && git clone --depth 1 https://github.com/TLeconte/acarsdec.git \
    && cd acarsdec \
    && mkdir build && cd build \
    && cmake .. -Drtl=ON -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    && make \
    && cp acarsdec /staging/usr/bin/acarsdec \
    && rm -rf /tmp/acarsdec

# Build libacars (required by dumpvdl2)
RUN cd /tmp \
    && git clone --depth 1 https://github.com/szpajder/libacars.git \
    && cd libacars \
    && mkdir build && cd build \
    && cmake .. \
    && make \
    && make install \
    && ldconfig \
    && cp -a /usr/local/lib/libacars* /staging/usr/local/lib/ \
    && rm -rf /tmp/libacars

# Build dumpvdl2 (VDL2 aircraft datalink decoder)
RUN cd /tmp \
    && git clone --depth 1 https://github.com/szpajder/dumpvdl2.git \
    && cd dumpvdl2 \
    && mkdir build && cd build \
    && cmake .. \
    && make \
    && cp src/dumpvdl2 /staging/usr/bin/dumpvdl2 \
    && rm -rf /tmp/dumpvdl2

# Build slowrx (SSTV decoder) — pinned to known-good commit
RUN cd /tmp \
    && git clone https://github.com/windytan/slowrx.git \
    && cd slowrx \
    && git checkout ca6d7012 \
    && make \
    && install -m 0755 slowrx /staging/usr/local/bin/slowrx \
    && rm -rf /tmp/slowrx

# Build SatDump (weather satellite decoder - NOAA APT & Meteor LRPT) — pinned to v1.2.2
# Split into compile (heavy, cached) and staging (light, safe to change) layers
RUN cd /tmp \
    && git clone --depth 1 --branch 1.2.2 https://github.com/SatDump/SatDump.git \
    && cd SatDump \
    && mkdir build && cd build \
    && ARCH_FLAGS=""; if [ "$(uname -m)" = "x86_64" ]; then ARCH_FLAGS="-march=x86-64"; fi \
    && cmake -DCMAKE_BUILD_TYPE=Release -DBUILD_GUI=OFF -DCMAKE_INSTALL_LIBDIR=lib \
           -DCMAKE_C_FLAGS="$ARCH_FLAGS" \
           -DCMAKE_CXX_FLAGS="$ARCH_FLAGS" .. \
    && make -j$(nproc) \
    && make install \
    && ldconfig \
    # Ensure SatDump plugins are in the expected path (handles multiarch differences)
    && mkdir -p /usr/local/lib/satdump/plugins \
    && if [ -z "$(ls /usr/local/lib/satdump/plugins/*.so 2>/dev/null)" ]; then \
        for dir in /usr/local/lib/*/satdump/plugins /usr/lib/*/satdump/plugins /usr/lib/satdump/plugins; do \
            if [ -d "$dir" ] && [ -n "$(ls "$dir"/*.so 2>/dev/null)" ]; then \
                ln -sf "$dir"/*.so /usr/local/lib/satdump/plugins/; \
                break; \
            fi; \
        done; \
    fi \
    && rm -rf /tmp/SatDump

# Stage SatDump artifacts (separate layer so compile cache survives staging changes)
# On arm64 cmake installs to /usr/{bin,lib,share}; on x86 to /usr/local/{bin,lib,share}
RUN mkdir -p /staging/usr/local/share /staging/usr/local/lib/satdump/plugins \
    # Binary
    && (cp -a /usr/local/bin/satdump /staging/usr/local/bin/ 2>/dev/null \
        || cp -a /usr/bin/satdump /staging/usr/local/bin/) \
    # Core shared library
    && (cp -a /usr/local/lib/libsatdump* /staging/usr/local/lib/ 2>/dev/null \
        || cp -a /usr/lib/libsatdump* /staging/usr/local/lib/) \
    # Plugins
    && (cp -a /usr/local/lib/satdump/plugins/*.so /staging/usr/local/lib/satdump/plugins/ 2>/dev/null \
        || cp -a /usr/lib/satdump/plugins/*.so /staging/usr/local/lib/satdump/plugins/ 2>/dev/null \
        || true) \
    # Pipeline definitions and resources
    && (cp -a /usr/local/share/satdump /staging/usr/local/share/ 2>/dev/null \
        || cp -a /usr/share/satdump /staging/usr/local/share/) \
    # Verify
    && test -x /staging/usr/local/bin/satdump \
    && ls /staging/usr/local/share/satdump/pipelines/*.json >/dev/null 2>&1 \
    && echo "SatDump staging OK: $(ls /staging/usr/local/share/satdump/pipelines/*.json | wc -l) pipeline files"

# Build hackrf CLI tools from source — avoids libhackrf0 version conflict
# between the 'hackrf' apt package and soapysdr-module-hackrf's newer libhackrf0
RUN cd /tmp \
    && git clone --depth 1 https://github.com/greatscottgadgets/hackrf.git \
    && cd hackrf/host \
    && mkdir build && cd build \
    && cmake .. \
    && make \
    && make install \
    && ldconfig \
    && cp -a /usr/local/bin/hackrf_* /staging/usr/local/bin/ 2>/dev/null || true \
    && cp -a /usr/local/lib/libhackrf* /staging/usr/local/lib/ 2>/dev/null || true \
    && rm -rf /tmp/hackrf

# Install radiosonde_auto_rx (weather balloon decoder)
RUN cd /tmp \
    && git clone --depth 1 https://github.com/projecthorus/radiosonde_auto_rx.git \
    && cd radiosonde_auto_rx/auto_rx \
    && pip install --no-cache-dir -r requirements.txt semver \
    && bash build.sh \
    && mkdir -p /staging/opt/radiosonde_auto_rx/auto_rx \
    && cp -r . /staging/opt/radiosonde_auto_rx/auto_rx/ \
    && chmod +x /staging/opt/radiosonde_auto_rx/auto_rx/auto_rx.py \
    && rm -rf /tmp/radiosonde_auto_rx

# Build rtlamr (utility meter decoder - requires Go)
RUN cd /tmp \
    && curl -fsSL "https://go.dev/dl/go1.22.5.linux-$(dpkg --print-architecture).tar.gz" | tar -C /usr/local -xz \
    && export PATH="$PATH:/usr/local/go/bin" \
    && export GOPATH=/tmp/gopath \
    && go install github.com/bemasher/rtlamr@latest \
    && cp /tmp/gopath/bin/rtlamr /staging/usr/bin/rtlamr \
    && rm -rf /usr/local/go /tmp/gopath

###############################################################################
# Stage 2: Runtime — lean image with only runtime dependencies
###############################################################################
FROM python:3.11-slim

LABEL maintainer="INTERCEPT Project"
LABEL description="Signal Intelligence Platform for SDR monitoring"

# Set working directory
WORKDIR /app

# Pre-accept tshark non-root capture prompt for non-interactive install
RUN echo 'wireshark-common wireshark-common/install-setuid boolean true' | debconf-set-selections

# Install ONLY runtime dependencies (no -dev packages, no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # RTL-SDR tools
    rtl-sdr \
    # 433MHz decoder
    rtl-433 \
    # Pager decoder
    multimon-ng \
    # Audio tools for Listening Post
    ffmpeg \
    # SSTV decoder runtime libs
    libsndfile1 \
    # SatDump runtime libs (weather satellite decoding)
    libpng16-16 \
    libtiff6 \
    libjemalloc2 \
    libfftw3-double3 \
    libfftw3-single3 \
    libvolk-bin \
    libnng1 \
    libzstd1 \
    # WiFi tools (aircrack-ng suite)
    aircrack-ng \
    iw \
    wireless-tools \
    # Bluetooth tools
    bluez \
    bluetooth \
    # GPS support
    gpsd \
    gpsd-clients \
    # APRS
    direwolf \
    # WiFi Extra
    hcxdumptool \
    hcxtools \
    # SDR Hardware & SoapySDR
    soapysdr-tools \
    soapysdr-module-rtlsdr \
    soapysdr-module-hackrf \
    soapysdr-module-lms7 \
    soapysdr-module-airspy \
    airspy \
    limesuite \
    # Utilities
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled binaries and libraries from builder stage
COPY --from=builder /staging/usr/bin/ /usr/bin/
COPY --from=builder /staging/usr/local/bin/ /usr/local/bin/
COPY --from=builder /staging/usr/local/lib/ /usr/local/lib/
COPY --from=builder /staging/usr/local/share/ /usr/local/share/
COPY --from=builder /staging/opt/ /opt/

# Copy radiosonde Python dependencies installed during builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/

# Refresh shared library cache for custom-built libraries
RUN ldconfig

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Strip Windows CRLF from shell scripts (git autocrlf can re-introduce them)
RUN find . -name '*.sh' -exec sed -i 's/\r$//' {} +

# Create data directory for persistence
RUN mkdir -p /app/data /app/data/weather_sat /app/data/radiosonde/logs

# Expose web interface port
EXPOSE 5050
EXPOSE 5443

# Environment variables with defaults
ENV INTERCEPT_HOST=0.0.0.0 \
    INTERCEPT_PORT=5050 \
    INTERCEPT_LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

# Health check using the new endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost:5050/health || exit 1

# Run the application
CMD ["/bin/bash", "start.sh"]
