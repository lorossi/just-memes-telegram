FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

# ffmpeg converts the videos, tesseract reads the text in the memes
RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr \
  && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
  UV_LINK_MODE=copy \
  UV_PYTHON_DOWNLOADS=never \
  PYTHONUNBUFFERED=1

WORKDIR /app

# install the dependencies first, so they are cached between builds
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked --no-install-project --no-default-groups

# then install the project itself
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked --no-default-groups

ENV PATH="/app/.venv/bin:$PATH"

# the settings and the database live in mounted volumes,
# so they survive container rebuilds
CMD ["just-memes-telegram"]
