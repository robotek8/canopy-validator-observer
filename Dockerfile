FROM python:3.12-alpine

WORKDIR /app

COPY canopy_observer.py /app/canopy_observer.py

RUN adduser -D -u 10001 observer \
    && mkdir -p /app/reports \
    && chown -R observer:observer /app

USER observer

ENTRYPOINT ["python", "/app/canopy_observer.py"]
