FROM python:3.12

WORKDIR /fastapi

# Install deps + local packages (app, untp) at build time; no `uv run` at runtime on OpenShift.
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
COPY untp ./untp
COPY config.py main.py ./

RUN pip install uv \
    && uv sync --frozen

ENV PATH="/fastapi/.venv/bin:$PATH"

CMD ["python", "main.py"]
