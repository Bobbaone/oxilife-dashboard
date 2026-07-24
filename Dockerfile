FROM python:3.12-slim
WORKDIR /app
ARG APP_VERSION=1.0.0
ARG APP_BUILD=local
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV APP_VERSION=${APP_VERSION}
ENV APP_BUILD=${APP_BUILD}
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY docker-entrypoint.py /usr/local/bin/docker-entrypoint.py
RUN groupadd --gid 10001 poolmonitor \
    && useradd --uid 10001 --gid poolmonitor --no-create-home --shell /usr/sbin/nologin poolmonitor \
    && mkdir -p /app/data \
    && chown -R poolmonitor:poolmonitor /app/data \
    && chmod 755 /usr/local/bin/docker-entrypoint.py
EXPOSE 8000
ENTRYPOINT ["python", "/usr/local/bin/docker-entrypoint.py"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
