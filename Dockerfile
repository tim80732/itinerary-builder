FROM python:3.12-slim

LABEL org.opencontainers.image.title="itinerary-builder"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    PORT=8080

# Project folders and export names may be CJK: keep the filesystem encoding
# UTF-8 whatever the container locale says.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

VOLUME ["/data"]
EXPOSE 8080

CMD ["python", "-m", "app.main"]
