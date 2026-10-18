FROM nvidia/cuda:13.0.2-devel-ubuntu24.04@sha256:5dc1bca23d05bd37b011be68ec470c03b403a5da07ec3a86e41af9470e9d0cc6 AS generic-build
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends g++ \
  && rm -rf /var/lib/apt/lists/*
COPY worker/generic/engine.cu worker/generic/ec_core.h /build/
RUN nvcc -std=c++17 -O2 -gencode arch=compute_75,code=sm_75 \
      -gencode arch=compute_80,code=sm_80 \
      -gencode arch=compute_86,code=sm_86 -gencode arch=compute_89,code=sm_89 \
      -gencode arch=compute_90,code=sm_90 -gencode arch=compute_100,code=sm_100 \
      -gencode arch=compute_120,code=sm_120 -gencode arch=compute_75,code=compute_75 \
      /build/engine.cu -o /build/generic-engine \
  && /build/generic-engine --self-test \
  && g++ -std=c++17 -O2 -fopenmp -x c++ /build/engine.cu -o /build/generic-engine-cpu \
  && /build/generic-engine-cpu --self-test

FROM ghcr.io/aburan28/ecc2k130-worker@sha256:25299db686c6b73bb59edc1b5f6591d917ada4e5d933172a4eca7274a71e9421
LABEL org.opencontainers.image.source="https://github.com/aburan28/ecc-challenges" \
      org.opencontainers.image.description="ECC Challenges CPU and CUDA worker for all 24 Certicom curves"
# Refresh Ubuntu security packages while retaining the reviewed CUDA and NCCL stack.
USER root
RUN apt-mark hold cuda-compat-13-3 cuda-toolkit-13-config-common \
      cuda-toolkit-config-common libnccl2 \
  && apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libgomp1 \
  && rm -rf /var/lib/apt/lists/*
COPY worker/client.py worker/protocol.py /opt/ecc2k130/
COPY worker/generic/ /opt/ecc2k130/generic/
COPY src/ecc_challenges/__init__.py /opt/ecc2k130/ecc_challenges/__init__.py
COPY src/ecc_challenges/multicurve/ /opt/ecc2k130/ecc_challenges/multicurve/
COPY --from=generic-build /build/generic-engine /opt/ecc/generic-engine
COPY --from=generic-build /build/generic-engine-cpu /opt/ecc/generic-engine-cpu
ENV ECC_API_KEY_FILE=/run/secrets/ecc_api_key ECC_API_URL=https://ecc-challenges.com
USER 10001:10001
ENTRYPOINT ["python3", "/opt/ecc2k130/client.py"]
