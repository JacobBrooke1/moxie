# Moxie in a container. Build & run:
#
#   docker build -t moxie .
#   docker run -d --name moxie \
#     -v moxie-home:/home/moxie/.moxie \
#     -p 127.0.0.1:8484:8484 \
#     --env-file .env \
#     moxie
#
# Publishing on 127.0.0.1 keeps the dash loopback-only on the HOST even
# though it binds 0.0.0.0 inside the container. If you must expose it
# further, set MOXIE_DASH_TOKEN. Secrets: pass via --env-file; the OS
# keychain isn't available in a container.
FROM python:3.12-slim

RUN useradd --create-home moxie
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY moxie/ moxie/
COPY skills/ skills/
RUN pip install --no-cache-dir ".[pdf,secure]"

USER moxie
ENV MOXIE_DASH_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1
EXPOSE 8484
VOLUME ["/home/moxie/.moxie"]

CMD ["moxie", "serve"]
