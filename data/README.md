# Datos

Nada de esta carpeta (salvo este archivo) se guarda en Git: cada subcarpeta se registra
con DVC y Git solo conserva su puntero `.dvc`.

| Carpeta | Qué contiene | Quién la escribe |
|---|---|---|
| `kaggle/` | Los 5 CSV originales de Kaggle | La persona que instala el producto |
| `bandeja/` | Sistema de origen simulado: carga inicial + un archivo por fecha de llegada y fuente | `bandeja-init` (`python -m reposicion.bandeja`); nunca el flujo |
| `historico/` | Datos ingeridos, con su fecha de llegada | DAG de ingesta |
| `procesado/` | Vista point-in-time de cada fuente por fecha de corte (entrada del pronóstico) | tarea `preparar_datos` de `pronostico_semanal` |

Detalle del formato de la bandeja en el README principal.
