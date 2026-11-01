# Lab1_PD · Motor de reposición de inventario

Producto de ML que, en cada fecha de corte, transforma las ventas que llegan a diario en:

1. **Pronóstico** de ventas diarias por tienda y familia de producto, con cuantiles.
2. **Pedido sugerido** al centro de distribución, según los costos de quiebre y merma
   definidos en [`config/params.yaml`](config/params.yaml).
3. **Seguimiento** del desempeño de los pronósticos ya publicados.

Stack: Git + GitHub Actions · DVC · Apache Airflow 3 (Docker Compose) · Python.

> Estado: estructura inicial. Las secciones marcadas *(pendiente)* se completan durante
> la implementación.

## Requisitos

- Git, Python 3.11+ y Docker Desktop (con WSL2 en Windows).
- Los datos de la competencia
  [Store Sales – Time Series Forecasting](https://www.kaggle.com/competitions/store-sales-time-series-forecasting/data).

## Puesta en marcha desde cero

```bash
git clone <url-del-repo> && cd Lab1_PD
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt dvc   # Linux/macOS: .venv/bin/pip
```

Colocar en `data/kaggle/` estos cinco archivos de Kaggle (sin renombrar):
`train.csv`, `stores.csv`, `holidays_events.csv`, `oil.csv`, `transactions.csv`.

### Levantar Airflow

```bash
docker compose up -d --build
```

- Interfaz: <http://localhost:8080> · usuario `airflow`, contraseña `airflow`.
- El DAG `verificar_entorno` (manual) comprueba que Airflow importa el paquete, lee
  `config/params.yaml` y que la bandeja está montada en solo lectura.
- Detener: `docker compose down` (agregar `-v` para borrar también los metadatos).

Generación de la bandeja y ejecución de los DAGs del producto: *(pendiente)*.

## Estructura

```
config/            parámetros versionados (costos, horizonte, cuantiles)
src/reposicion/    lógica del producto
dags/              DAGs de Airflow (solo orquestan)
tests/             pruebas con datos sintéticos
data/              datos (DVC) · ver data/README.md
modelos/           modelos entrenados (DVC)
publicados/        pronósticos y pedidos emitidos (DVC)
```

## Versionado de datos

Los datos y modelos se versionan con DVC sobre un remoto local (carpeta fuera del repo).
Configuración y justificación en [DECISIONES.md](DECISIONES.md).

## Reproducción de un pronóstico pasado *(pendiente)*

Comandos utilizados y resultados obtenidos.

## Evidencia de funcionamiento *(pendiente)*

Ejecuciones en Airflow, comparación de modelos y reporte de seguimiento.
