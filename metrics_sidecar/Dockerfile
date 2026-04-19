FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

# Метрик-порт + добавление стандартных корп. меток для Docker SD.
# Реальный скрейпинг происходит через VictoriaMetrics на prod, в dev эти
# labels просто лежат без скрейпера.
LABEL prometheus_scrape="true"
LABEL prometheus_port="8000"
LABEL prometheus_path="/metrics"
LABEL app_name="hermes-metrics"
LABEL app_type="backend"
LABEL version="0.2.0"

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
