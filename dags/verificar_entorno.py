"""DAG manual que comprueba que Airflow ve el paquete, la configuración y los montajes."""

from datetime import datetime

from airflow.sdk import dag, task


@dag(
    dag_id="verificar_entorno",
    schedule=None,
    start_date=datetime(2017, 1, 1),
    catchup=False,
    tags=["infraestructura"],
)
def verificar_entorno():
    @task
    def diagnosticar() -> dict:
        from reposicion.entorno import diagnostico

        resultado = diagnostico()
        if resultado["bandeja_escribible"]:
            raise RuntimeError("La bandeja está montada con escritura; debe ser :ro.")
        return resultado

    diagnosticar()


verificar_entorno()
