FROM python:3.12

WORKDIR /fastapi

# Install deps + local packages (app, untp) at build time; no `uv run` at runtime on OpenShift.
# Build context is the repository root (see .github/workflows/image-publisher.yaml).
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
COPY backend/app ./app
COPY backend/untp ./untp
COPY backend/config.py backend/main.py backend/witness.py ./

# Repo-root credential / publication configs (CONFIG_ROOT=/config in container).
COPY configs /config
ENV CONFIG_ROOT=/config

RUN pip install uv \
    && uv sync --frozen

ENV PATH="/fastapi/.venv/bin:$PATH"

CMD ["python", "main.py"]
