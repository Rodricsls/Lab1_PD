"""Pronóstico y pedido sugerido semanal (lunes).

esperar_ingesta → revisar_modelo → publicar

- `esperar_ingesta` (sensor en modo reschedule) espera a que la ingesta haya procesado
  todas las llegadas hasta la fecha lógica, de modo que el pronóstico nunca usa datos
  incompletos aunque los DAGs corran en paralelo durante un backfill.
- `revisar_modelo` reentrena cada `reentrenar_cada_semanas` con backtest y criterio de
  reemplazo; en las demás semanas usa el modelo vigente.
- `publicar` escribe pronóstico, pedido y metadata en `publicados/fecha_corte=<fecha>/`.
"""

from datetime import datetime, timedelta

from airflow.sdk import PokeReturnValue, dag, task


@dag(
    dag_id="pronostico_semanal",
    schedule="0 0 * * 1",
    start_date=datetime(2017, 1, 2),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    tags=["pronostico"],
)
def pronostico_semanal():
    # soft_fail: si la ingesta nunca termina, la corrida queda omitida en lugar de fallar.
    @task.sensor(poke_interval=60, timeout=6 * 3600, mode="reschedule", soft_fail=True)
    def esperar_ingesta(logical_date=None) -> PokeReturnValue:
        from airflow.sdk.exceptions import AirflowSkipException

        from reposicion.entorno import contexto
        from reposicion.produccion import dias_sin_ingerir, ultima_llegada

        _, rutas = contexto()
        fin = ultima_llegada(rutas["bandeja"])
        if fin is None or logical_date.date() > fin:
            # Fecha fuera del periodo con datos (p. ej. la corrida de "hoy" al activar el DAG).
            raise AirflowSkipException(f"La bandeja no tiene datos para {logical_date.date()}.")
        faltan = dias_sin_ingerir(rutas["historico"], logical_date.date())
        if faltan:
            print(f"Faltan {len(faltan)} días por ingerir; primero: {faltan[0]}")
        return PokeReturnValue(is_done=not faltan)

    @task
    def revisar_modelo(logical_date=None) -> dict:
        from reposicion.entorno import contexto
        from reposicion.produccion import revisar_modelo

        params, rutas = contexto()
        return revisar_modelo(rutas["historico"], rutas["modelos"], params, logical_date.date())

    @task
    def publicar(logical_date=None) -> dict:
        from reposicion.entorno import contexto, raiz_proyecto
        from reposicion.produccion import publicar

        params, rutas = contexto()
        return publicar(raiz_proyecto(), rutas, params, logical_date.date())

    esperar_ingesta() >> revisar_modelo() >> publicar()


pronostico_semanal()
