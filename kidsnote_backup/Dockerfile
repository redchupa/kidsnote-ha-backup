FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py archive.py client.py doc_text.py kidsnote_auth.py pacing.py supplies.py viewer.py ./
COPY web/ ./web/
CMD ["python", "/app/app.py"]
