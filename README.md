# Lab1_PD · Motor de reposición de inventario

Producto de ML que, en cada fecha de corte, transforma las ventas que llegan a diario en:

1. **Pronóstico** de ventas diarias por tienda y familia de producto, con cuantiles.
2. **Pedido sugerido** al centro de distribución, según los costos de quiebre y merma
   definidos en [`config/params.yaml`](config/params.yaml).
3. **Seguimiento** del desempeño de los pronósticos ya publicados.

Stack: Git + GitHub Actions · DVC · Apache Airflow 3 (Docker Compose) · Python.

> Estado: en implementación. Las secciones marcadas *(pendiente)* se completan más adelante.

## Requisitos

- Git y Docker Desktop (con WSL2 en Windows), con al menos 4 GB de RAM asignados.
- Los datos de la competencia
  [Store Sales – Time Series Forecasting](https://www.kaggle.com/competitions/store-sales-time-series-forecasting/data).

## Puesta en marcha desde cero

Solo se necesitan Docker y los CSV de Kaggle:

```bash
git clone https://github.com/Rodricsls/Lab1_PD.git && cd Lab1_PD
```

1. Colocar en **`data/kaggle/`** estos cinco archivos de Kaggle, sin renombrar:
   `train.csv`, `stores.csv`, `holidays_events.csv`, `oil.csv`, `transactions.csv`.
2. Solo en Linux: para que los archivos generados queden a nombre de su usuario,

   ```bash
   echo "AIRFLOW_UID=$(id -u)" > .env
   ```

3. Levantar todo:

   ```bash
   docker compose up -d --build
   ```

   Al arrancar, el servicio `bandeja-init` construye la carga histórica y la bandeja a
   partir de esos CSV (≈ 1–2 min la primera vez) y solo entonces arranca Airflow. Si
   faltan archivos o columnas, `bandeja-init` falla con un mensaje que dice cuál:
   `docker compose logs bandeja-init`.

- Interfaz: <http://localhost:8080> · usuario `airflow`, contraseña `airflow`.
- El DAG `verificar_entorno` (manual) comprueba que Airflow importa el paquete, lee
  `config/params.yaml` y que la bandeja está montada en solo lectura.
- Detener: `docker compose down` (agregar `-v` para borrar también los metadatos).

### Ejecutar el flujo sobre un periodo

```bash
docker compose exec airflow-scheduler airflow dags unpause ingesta_diaria
docker compose exec airflow-scheduler airflow backfill create --dag-id ingesta_diaria \
  --from-date 2017-01-01 --to-date 2017-08-16 --max-active-runs 8
```

| DAG | Qué hace |
|---|---|
| `ingesta_diaria` | Para la fecha lógica D: ingiere la carga inicial (una vez) y, por fuente, lo que llegó a la bandeja en D; valida y registra en `data/historico/`. Un archivo inválido deja en rojo la tarea de su fuente y no entra; las situaciones esperadas quedan como avisos en `data/historico/_reportes/llegada=D/`. |

Pronóstico, pedido y seguimiento: *(pendiente)*.

### Desarrollo local (opcional)

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt dvc -e .   # Linux/macOS: .venv/bin/pip
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m reposicion.bandeja      # misma generación que bandeja-init
```

## Bandeja: el sistema de origen simulado

La bandeja se organiza por **fecha de llegada**, de modo que en una fecha lógica D el
flujo ve solo lo que había llegado hasta D:

```
data/bandeja/carga_inicial/<fuente>.csv                         todo lo llegado hasta el corte
data/bandeja/<fuente>/llegada=AAAA-MM-DD/<fuente>_<clave>.csv   entregas diarias
data/bandeja/_manifiesto.csv                                    cada versión entregada
```

| Fuente | Origen en Kaggle | Llega |
|---|---|---|
| `ventas` | `train.csv` (sin `onpromotion`) | D+1 |
| `transacciones` | `transactions.csv` | D+2 |
| `petroleo` | `oil.csv` (solo días hábiles) | D+1 |
| `promociones` | `onpromotion` de `train.csv` | 14 días antes de D |
| `feriados` | `holidays_events.csv`, un archivo por mes | día 15 del mes anterior |
| `tiendas` | `stores.csv` | en la carga inicial |

- **Corte histórico:** 2016-12-31; la bandeja llega hasta la última fecha de `train.csv`.
  Si los CSV traen más fechas, la bandeja se extiende sola.
- **Correcciones:** el mismo archivo en una carpeta de llegada posterior; gana la llegada
  más reciente. Se generan de dos formas: ≈ 2 % de los archivos de ventas llegan primero
  con errores y se corrigen 2–5 días después (simulado, con semilla), y si un CSV de Kaggle
  cambia entre ejecuciones, las fechas afectadas se entregan de nuevo como corrección.
- Todos estos valores están en la sección `bandeja` de `config/params.yaml`.
  Diseño y alternativas: [DECISIONES.md](DECISIONES.md) (D4).

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
