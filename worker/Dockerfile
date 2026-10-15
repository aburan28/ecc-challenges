FROM ghcr.io/aburan28/ecc2k130-worker@sha256:25299db686c6b73bb59edc1b5f6591d917ada4e5d933172a4eca7274a71e9421
LABEL org.opencontainers.image.source="https://github.com/aburan28/ecc-challenges"
COPY worker/client.py worker/protocol.py /opt/ecc2k130/
ENV ECC_API_KEY_FILE=/run/secrets/ecc_api_key ECC_API_URL=https://ecc-challenges.com
USER 10001:10001
ENTRYPOINT ["python3", "/opt/ecc2k130/client.py"]
