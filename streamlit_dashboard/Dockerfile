FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    streamlit==1.42.0 \
    pandas==2.2.* \
    numpy==1.26.* \
    psycopg2-binary==2.9.*

COPY main.py /app/main.py
COPY pages/ /app/pages/
COPY shared/ /app/shared/

ENV PYTHONPATH=/app
EXPOSE 8501

CMD ["streamlit", "run", "main.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
