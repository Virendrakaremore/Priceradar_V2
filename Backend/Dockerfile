FROM mcr.microsoft.com/playwright/python:v1.56.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# web:    gunicorn -w 2 --threads 8 -b 0.0.0.0:5000 wsgi:app
# worker: python -m priceradar.worker
CMD ["gunicorn", "-w", "2", "--threads", "8", "-b", "0.0.0.0:5000", "wsgi:app"]
