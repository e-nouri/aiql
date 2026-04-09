FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m spacy download xx_ent_wiki_sm

COPY . .

CMD ["arq", "worker.settings.WorkerSettings"]
