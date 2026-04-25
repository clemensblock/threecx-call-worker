FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml && \
    find /usr/local/lib/python3.12/site-packages \
      \( -type d -name __pycache__ -o -type d -name "*.dist-info" \
         -o -type d -name tests -o -type d -name test \) \
      -exec rm -rf {} + 2>/dev/null; \
    rm -rf /usr/local/lib/python3.12/site-packages/pip \
           /usr/local/lib/python3.12/site-packages/pygments \
           /usr/local/lib/python3.12/site-packages/hive_metastore \
           /usr/local/lib/python3.12/site-packages/pyiceberg \
           /usr/local/lib/python3.12/site-packages/zstandard \
           /usr/local/lib/python3.12/site-packages/rich \
    ; true

COPY worker/ ./worker/

FROM python:3.12-slim

RUN groupadd -r worker && useradd -r -g worker -s /sbin/nologin worker

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY --from=builder /build/worker ./worker

USER worker

EXPOSE 8000

CMD ["uvicorn", "worker.main:app", "--host", "0.0.0.0", "--port", "8000"]
