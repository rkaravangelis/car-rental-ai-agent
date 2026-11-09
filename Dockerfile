# One image for all our Python services.
FROM python:3.12-slim

WORKDIR /app

# pip >= 25.1 is needed for "pip install --group" (dependency groups in pyproject.toml)
RUN pip install --no-cache-dir -U pip

# CPU-only PyTorch first. Otherwise sentence-transformers pulls the multi-GB GPU build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Dependencies before code: this layer is cached and only rebuilt when pyproject.toml changes
COPY pyproject.toml .
RUN pip install --no-cache-dir --group app

# Bake the embedding model into the image: no download at runtime, works offline in the demo
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Our code last: changes here rebuild only this quick layer
COPY . .

# Default command (overridden per service in docker-compose.yml)
CMD ["python", "03_rag/ingest.py"]
