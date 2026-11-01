# Imagen de Airflow con las dependencias del producto.
FROM apache/airflow:3.3.2-python3.12

# LightGBM necesita la librería de OpenMP del sistema.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

USER airflow
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
