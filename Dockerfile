FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS runtime
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY src ./src
RUN uv sync --no-dev
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "yowayowa.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
