"""Ingesta y validación diaria de la bandeja.

Para la fecha lógica D, cada fuente ingiere lo que llegó a `data/bandeja/<fuente>/llegada=D/`.
Un archivo inválido deja su tarea en rojo sin reintentos (un error de datos falla igual en
cada intento) y no entra al histórico; las demás fuentes siguen. La lógica vive en
`reposicion.ingesta`.
"""

from datetime import datetime, timedelta

from airflow.sdk import dag, task

FUENTES = ["ventas", "transacciones", "petroleo", "promociones", "feriados"]


@dag(
    dag_id="ingesta_diaria",
    schedule="@daily",
    start_date=datetime(2017, 1, 1),
    catchup=False,
    max_active_runs=8,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=1)},
    tags=["ingesta"],
)
def ingesta_diaria():
    @task
    def carga_inicial() -> dict:
        from reposicion.entorno import contexto
        from reposicion.ingesta import ingerir_carga_inicial

        _, rutas = contexto()
        return ingerir_carga_inicial(rutas["bandeja"], rutas["historico"])

    @task
    def ingerir(fuente: str, logical_date=None) -> dict:
        from airflow.sdk.exceptions import AirflowFailException

        from reposicion.entorno import contexto
        from reposicion.ingesta import ingerir_dia
        from reposicion.validacion import DatosInvalidos

        params, rutas = contexto()
        try:
            return ingerir_dia(
                fuente, logical_date.date(), rutas["bandeja"], rutas["historico"], params
            )
        except DatosInvalidos as e:
            raise AirflowFailException(str(e)) from e

    @task(trigger_rule="all_done")
    def resumen(logical_date=None) -> dict:
        from reposicion.entorno import contexto
        from reposicion.ingesta import resumir_dia

        _, rutas = contexto()
        return resumir_dia(logical_date.date(), rutas["historico"])

    tareas = [ingerir.override(task_id=f"ingerir_{f}")(fuente=f) for f in FUENTES]
    carga_inicial() >> tareas >> resumen()


ingesta_diaria()
