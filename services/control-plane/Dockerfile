FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml .
COPY control_plane ./control_plane
RUN pip install --no-cache-dir .

RUN useradd --uid 10001 --create-home control-plane \
    && mkdir -p /data \
    && chown control-plane:control-plane /data
USER control-plane

EXPOSE 8090
CMD ["uvicorn", "control_plane.main:app", "--host", "0.0.0.0", "--port", "8090"]
