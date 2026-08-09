FROM python:3.11-slim

WORKDIR /app

# Install uv and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libgomp1 \
    libpq-dev \
    tesseract-ocr \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin/:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md LICENSE ./
COPY uv.lock ./
COPY app ./app
COPY docs ./docs
COPY data ./data

# Install dependencies using uv
RUN uv sync --frozen --no-dev --extra ocr

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
