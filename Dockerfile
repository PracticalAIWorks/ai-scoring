FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py excel_export.py backup.py rubrics.json ./
COPY static ./static
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
RUN mkdir /data && useradd --uid 10001 appuser && chown appuser:appuser /data \
    && chmod -R a+rX /app
USER appuser
EXPOSE 8000
CMD ["waitress-serve", "--listen=0.0.0.0:8000", "--threads=8", "app:application"]
