FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PATHWAY_TELEMETRY_LEVEL=none
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app/watchtower
CMD ["python", "-m", "watchtower.service"]
