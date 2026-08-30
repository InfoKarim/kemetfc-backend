FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgl1 libglib2.0-0 libgles2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-vision.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-vision.txt

COPY . .

# The downloader verifies the pinned model checksum before installation.
RUN python scripts/download_pose_model.py

RUN useradd --create-home --uid 10001 trainingbuddy \
    && chown -R trainingbuddy:trainingbuddy /app

USER trainingbuddy

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
