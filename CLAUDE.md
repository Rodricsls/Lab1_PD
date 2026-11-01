# Contexto para agentes · Lab1_PD

Producto: **motor de reposición de inventario** para una cadena de supermercados
(datos de Corporación Favorita). En cada fecha de corte emite un pronóstico por
cuantiles por tienda × familia, un pedido sugerido y un reporte de desempeño de
pronósticos anteriores. Lo difícil no es el modelo, es operar en el tiempo:
datos que llegan por partes, tarde o corregidos.

## Mapa del repositorio

| Ruta | Contenido | Versionado |
|---|---|---|
| `src/reposicion/` | Toda la lógica (paquete Python) | Git |
| `dags/` | DAGs de Airflow: solo orquestación | Git |
| `config/params.yaml` | Costos, horizonte, cuantiles, umbrales | Git |
| `tests/` | pytest con datos sintéticos pequeños | Git |
| `data/kaggle/` | CSV originales de Kaggle (entrada) | DVC |
| `data/bandeja/` | Sistema de origen simulado: un archivo por fecha y fuente | DVC |
| `data/historico/`, `data/procesado/` | Datos ingeridos y tablas de modelado | DVC |
| `modelos/`, `publicados/` | Registro de modelos; pronósticos, pedidos y seguimiento emitidos | DVC |
| `reportes/`, `docs/evidencia/` | Comparación de modelos, seguimiento y capturas de Airflow | Git |

## Invariantes (si un cambio rompe uno, el cambio está mal)

- **I1 · Punto en el tiempo.** Lo que se calcula para una fecha de corte usa solo
  registros cuya *fecha de llegada* es ≤ esa fecha. Toda función de features recibe
  `fecha_corte` de forma explícita.
- **I2 · Sin reloj.** Prohibido `datetime.now()`, `date.today()` o similares en `src/`
  y `dags/`. La fecha viene de `logical_date` (Airflow) o de un argumento.
- **I3 · Bandeja intocable.** `data/bandeja/` se lee, nunca se escribe desde el flujo.
  En Docker se monta `:ro`.
- **I4 · Idempotencia.** Cada salida se escribe en su partición
  (`.../fecha_corte=AAAA-MM-DD/`) y se reemplaza completa: escribir en temporal y
  renombrar. Nunca append.
- **I5 · DAGs delgados.** Un `@task` llama a una función de `reposicion` y devuelve
  rutas o conteos (XCom). Nada de DataFrames por XCom ni lógica de negocio en `dags/`.
- **I6 · Dos tipos de problema.** Dato inválido (esquema roto, claves desconocidas,
  valores imposibles) → lanzar excepción y detener. Situación de negocio esperada
  (feriado, tienda cerrada, fuente que aún no llega) → manejar y registrar, sin fallar.
- **I7 · Configuración, no constantes.** Costos, cuantiles, horizonte y umbrales se leen
  de `config/params.yaml` vía `reposicion.config.cargar_params`.
- **I8 · Datos fuera de Git.** Ningún CSV, Parquet o modelo entra a Git. Se registra con
  `dvc add`, se sube con `dvc push` y solo se commitea el `.dvc`.

## Forma de trabajar

- `main` está protegida. Cada tarea: rama `tipo/descripcion-corta`
  (`feat/`, `fix/`, `test/`, `docs/`, `chore/`, `data/`, `exp/`) → PR → squash merge.
- Mensajes Conventional Commits con alcance: `feat(ingesta): detectar archivos corregidos`.
- Un commit = un cambio lógico. Agregar archivos por nombre (no `git add .`) y revisar
  `git diff --staged` antes de commitear.
- Nunca `--force` / `--force-with-lease`, nunca commitear directo a `main`.
- Si cambian datos: `dvc add` → `dvc push` → commit del `.dvc` → `git push`.
- Antes de abrir un PR: `ruff check .` y `pytest -q` en verde.
- Toda decisión no obvia (supuesto, alternativa descartada) se anota en `DECISIONES.md`.

## Comandos

```bash
.venv/Scripts/python -m pytest -q      # pruebas (Windows)
.venv/Scripts/ruff check .              # lint
.venv/Scripts/dvc status                # datos pendientes de registrar/subir
```
