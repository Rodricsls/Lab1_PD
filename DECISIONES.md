# Decisiones técnicas y de producto

Cada decisión registra contexto, alternativas consideradas, la elegida y por qué.

## D1 · Remoto de DVC: carpeta local

- **Contexto:** los datos (train.csv ≈ 120 MB) superan el límite de GitHub y deben
  versionarse fuera de Git.
- **Alternativas:** carpeta local, DagsHub, S3, GCS, Google Drive.
- **Elegida:** carpeta local fuera del repositorio, como en clase.
- **Por qué:** cero credenciales y cero costo; la reproducción se demuestra localmente.
  Consecuencia: quien clone el repo no puede hacer `dvc pull`; reconstruye los datos desde
  los CSV de Kaggle con el flujo documentado en el README.

## Pendientes

- Cadencia de pedido y horizonte del pronóstico.
- Costos de quiebre y merma (supuestos de negocio).
- Diseño de la bandeja: frecuencia y latencia por fuente, formato de correcciones.
- Disponibilidad de la información en el tiempo (fecha de llegada vs. fecha del dato).
- Reglas de validación: qué detiene el flujo y qué se tolera.
- Manejo de correcciones y reprocesamiento.
- Baseline, modelo de ML, esquema de validación temporal y criterio de promoción.
