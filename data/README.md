# Datos

Nada de esta carpeta (salvo este archivo) se guarda en Git: cada subcarpeta se registra
con DVC y Git solo conserva su puntero `.dvc`.

| Carpeta | Qué contiene | Quién la escribe |
|---|---|---|
| `kaggle/` | Los 5 CSV originales de Kaggle | La persona que instala el producto |
| `bandeja/` | Sistema de origen simulado: un archivo por fecha y fuente | Script generador (nunca el flujo) |
| `historico/` | Datos ingeridos, con su fecha de llegada | DAG de ingesta |
| `procesado/` | Tablas de modelado por fecha de corte | DAG de preparación |
