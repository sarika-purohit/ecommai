# The agent API for Cloud Run. Build from the repo root:
#   docker build -f agent/Dockerfile -t ecommai-agent .
# Settings come from the environment (Cloud Run variables and Secret Manager); no keys are baked in.

FROM python:3.13-slim AS deps
RUN pip install --no-cache-dir uv==0.9.18
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.txt \
    && uv venv /venv \
    && uv pip install --python /venv/bin/python --no-cache -r requirements.txt

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/venv/bin:$PATH \
    PORT=8080
COPY --from=deps /venv /venv
WORKDIR /app
COPY agent/ agent/
COPY data_prep/ data_prep/
RUN useradd --uid 10001 --no-create-home agent
USER agent
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn agent.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
