FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY eccpool ./eccpool
COPY challenges ./challenges
RUN pip install --no-cache-dir .
RUN useradd -r -u 10001 eccpool && mkdir /data && chown eccpool /data
USER eccpool
ENV ECCPOOL_DB=/data/eccpool.sqlite3
EXPOSE 8000
CMD ["eccpool", "serve", "--host", "0.0.0.0", "--port", "8000"]
