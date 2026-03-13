FROM python:3.10-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    # Playwright / Chromium deps
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libx11-xcb1 libxcb-dri3-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Dependency layer (cached unless requirements.txt changes) ---
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install only Chromium browser (not all Playwright browsers)
RUN playwright install chromium --with-deps

# --- Code is mounted from host via volume ---
# No COPY of source — we mount /opt/ouroboros_repo:/app at runtime.
# This preserves git workflow: git pull on host → restart container.

CMD ["python", "vps_launcher.py"]
