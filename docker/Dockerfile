FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    git build-essential libpango-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 libffi-dev shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN solc-select install 0.8.20 && solc-select use 0.8.20

COPY . .

# Build the knowledge base at image-build time so the container is
# ready to serve immediately (skip this line if you'd rather mount a
# pre-built chroma_store volume instead).
RUN python -m scripts.build_knowledge_base

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
