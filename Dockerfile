FROM python:3.11-slim

WORKDIR /app

COPY generate_m3u.py .

RUN pip install --no-cache-dir requests flask

CMD ["python", "generate_m3u.py"]
