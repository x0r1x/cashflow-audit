FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev \
    && uv pip install --no-deps .

ENV DATA_DIR=/data
EXPOSE 8080
CMD ["uv", "run", "--no-dev", "cashflow-audit", "serve", "--host", "0.0.0.0", "--port", "8080"]
