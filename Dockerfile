# =========================================================
# Stage 1: Builder
# =========================================================
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# pip --retries does NOT recover from IncompleteRead (mid-stream connection drops),
# which is the failure mode on this network. Wrap each install in a shell retry loop
# (up to 5 attempts) so a broken download is simply re-attempted.
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
    for i in 1 2 3 4 5; do \
      /opt/venv/bin/pip install --retries 10 --timeout 180 torch --index-url https://download.pytorch.org/whl/cpu && break || \
      { echo "torch attempt $i failed, retrying..."; sleep 5; }; \
    done && \
    for i in 1 2 3 4 5; do \
      /opt/venv/bin/pip install --retries 10 --timeout 180 -r requirements.txt && break || \
      { echo "requirements attempt $i failed, retrying..."; sleep 5; }; \
    done && \
    for i in 1 2 3 4 5; do \
      /opt/venv/bin/pip install --no-cache-dir --force-reinstall --retries 10 --timeout 180 "azure-mgmt-resource>=23.0.0" && break || \
      { echo "azure-mgmt-resource attempt $i failed, retrying..."; sleep 5; }; \
    done


# =========================================================
# Stage 2: Runtime
# =========================================================
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git wget unzip ca-certificates postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Terraform
ENV TERRAFORM_VERSION=1.14.9
RUN wget https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip && \
    unzip terraform_${TERRAFORM_VERSION}_linux_amd64.zip && \
    mv terraform /usr/local/bin/terraform && \
    chmod +x /usr/local/bin/terraform && \
    rm terraform_${TERRAFORM_VERSION}_linux_amd64.zip

RUN terraform version

# Infracost
ENV INFRACOST_VERSION=v0.10.39
RUN wget -q "https://github.com/infracost/infracost/releases/download/${INFRACOST_VERSION}/infracost-linux-amd64.tar.gz" \
    -O /tmp/infracost.tar.gz && \
    tar -xzf /tmp/infracost.tar.gz -C /usr/local/bin \
        --strip-components=0 infracost-linux-amd64 && \
    mv /usr/local/bin/infracost-linux-amd64 /usr/local/bin/infracost && \
    chmod +x /usr/local/bin/infracost && \
    rm /tmp/infracost.tar.gz

# Terraform plugin cache — persiste entre les migrations dans le même container
ENV TF_PLUGIN_CACHE_DIR=/tmp/tf-plugins
RUN mkdir -p /tmp/tf-plugins

# Python env
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Checkov (security scanner) — installed after venv is copied
RUN /opt/venv/bin/pip install --no-cache-dir checkov

ENV PYTHONPATH="/app"
ENV HF_HOME=/tmp/huggingface \
    HF_HUB_CACHE=/tmp/huggingface/hub \
    SENTENCE_TRANSFORMERS_HOME=/tmp/huggingface/st

# Pre-bake the embedding model (~420 MB) BEFORE copying source code.
# This layer is cached as long as pip dependencies don't change —
# code changes no longer trigger a re-download.
ARG HF_TOKEN
RUN mkdir -p /tmp/huggingface/hub /tmp/huggingface/transformers /tmp/huggingface/st && \
    HF_TOKEN=${HF_TOKEN} /opt/venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; \
     SentenceTransformer('sentence-transformers/all-mpnet-base-v2')" \
    || echo "WARNING: model pre-download skipped (offline build) — will download at first use"

# =========================================================
# COPY CODE — after model bake so code changes don't bust the model cache
# =========================================================
COPY app.py /app/app.py
COPY api /app/api
COPY services /app/services
COPY pipeline /app/pipeline
COPY rag /app/rag
COPY data /app/data
COPY configuration /app/configuration
COPY core /app/core
COPY agents /app/agents
COPY executor /app/executor
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY db_utils /app/db_utils
COPY requirements.txt /app/requirements.txt

# user
RUN useradd -m appuser

# dirs runtime — chmod 777 sur output/tf-plugins pour les bind mounts Windows (uid mismatch)
RUN mkdir -p /app/output /app/output2 /app/logs \
    /tmp/huggingface/hub /tmp/huggingface/transformers /tmp/huggingface/st \
    /tmp/tf-plugins && \
    chown -R appuser:appuser /app /tmp/huggingface /tmp/tf-plugins && \
    chmod -R 777 /app/output /app/output2 /app/logs /tmp/tf-plugins

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]