"""Seguimiento diario del desempeño de los pronósticos publicados.

esperar_ingesta → evaluar

Para la fecha lógica D compara cada publicación con fecha de corte ≤ D contra las ventas
conocidas en D. La lógica vive en `reposicion.seguimiento`.
"""

from datetime import datetime, timedelta

from airflow.sdk import PokeReturnValue, dag, task


@dag(
    dag_id="seguimiento_diario",
    schedule="@daily",
    start_date=datetime(2017, 1, 2),
    catchup=False,
    max_active_runs=8,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=1)},
    tags=["seguimiento"],
)
def seguimiento_diario():
    @task.sensor(poke_interval=60, timeout=6 * 3600, mode="reschedule", soft_fail=True)
    def esperar_ingesta(logical_date=None) -> PokeReturnValue:
        from airflow.sdk.exceptions import AirflowSkipException

        from reposicion.entorno import contexto
        from reposicion.produccion import dias_sin_ingerir, ultima_llegada

        _, rutas = contexto()
        fin = ultima_llegada(rutas["bandeja"])
        if fin is None or logical_date.date() > fin:
            raise AirflowSkipException(f"La bandeja no tiene datos para {logical_date.date()}.")
        faltan = dias_sin_ingerir(rutas["historico"], logical_date.date())
        return PokeReturnValue(is_done=not faltan)

    @task
    def evaluar(logical_date=None) -> dict:
        from reposicion.entorno import contexto
        from reposicion.seguimiento import escribir_seguimiento, resumen_global, seguimiento_al

        params, rutas = contexto()
        fecha = logical_date.date()
        df = seguimiento_al(rutas["historico"], rutas["publicados"], params, fecha)
        escribir_seguimiento(rutas["publicados"], fecha, df)
        return resumen_global(df, params)

    esperar_ingesta() >> evaluar()


seguimiento_diario()
