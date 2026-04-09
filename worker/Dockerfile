FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m spacy download xx_ent_wiki_sm && \
    python -c "import nltk; nltk.download('stopwords')"

COPY . .

CMD ["arq", "worker.settings.WorkerSettings"]
