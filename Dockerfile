FROM rust:1-slim AS stark
WORKDIR /src
COPY stark ./stark
RUN cargo build --release --manifest-path stark/Cargo.toml

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY eccpool ./eccpool
COPY challenges ./challenges
RUN pip install --no-cache-dir .
COPY --from=stark /src/stark/target/release/eccstark /usr/local/bin/eccstark
RUN useradd -r -u 10001 eccpool && mkdir /data && chown eccpool /data
USER eccpool
ENV ECCPOOL_DB=/data/eccpool.sqlite3 ECCPOOL_STARK_BIN=/usr/local/bin/eccstark
EXPOSE 8000
CMD ["eccpool", "serve", "--host", "0.0.0.0", "--port", "8000"]
