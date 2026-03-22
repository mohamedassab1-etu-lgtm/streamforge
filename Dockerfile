FROM python:3.10-slim

# Install ffmpeg, Deno, and dependencies
RUN apt-get update && apt-get install -y ffmpeg curl unzip && \
    curl -fsSL https://deno.land/install.sh | sh && \
    rm -rf /var/lib/apt/lists/*

# Add Deno to PATH
ENV DENO_INSTALL="/root/.deno"
ENV PATH="$DENO_INSTALL/bin:$PATH"

WORKDIR /app

# Copy requirements.txt first to install all standard libs (including rich)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Force update to the 2026 nightly bypass and POT provider
RUN pip install --no-cache-dir -U --pre "yt-dlp[default]"
RUN pip install --no-cache-dir -U bgutil-ytdlp-pot-provider

# Copy the rest of the project
COPY . .

EXPOSE 5000
CMD ["python", "-m", "api.main"]