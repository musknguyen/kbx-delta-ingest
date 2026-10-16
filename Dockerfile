FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Accepts either GEMINI_API_KEY or API_KEY:
#   docker run --rm -e API_KEY=... kb-sync            -> runs the sync once, exits 0
#   docker run --rm -e API_KEY=... kb-sync main.py    -> same
#   docker run --rm -e API_KEY=... kb-sync main.py ask "How do I add a YouTube video?"
ENTRYPOINT ["python"]
CMD ["main.py"]
