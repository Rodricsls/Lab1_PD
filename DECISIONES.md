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

## D2 · Protección de `main` sin aprobación obligatoria

- **Contexto:** el laboratorio es individual y GitHub no permite aprobar un PR propio.
- **Elegida:** ruleset `proteger-main` con PR obligatorio, check de CI `lint-y-pruebas`
  exigido (rama al día con `main`), solo squash merge, historial lineal, sin force push ni
  borrado; 0 aprobaciones requeridas.
- **Por qué:** conserva todas las garantías automáticas; la revisión humana la hace el autor
  leyendo el diff de cada PR antes de integrarlo.

## D3 · Airflow con LocalExecutor y el repositorio montado

- **Alternativas:** compose oficial con CeleryExecutor (Redis + worker + triggerer) o
  LocalExecutor.
- **Elegida:** LocalExecutor: postgres, api-server, scheduler y dag-processor.
- **Por qué:** un solo equipo no necesita workers distribuidos; menos contenedores y menos RAM.
- **Montajes:** el repo se monta en `/opt/lab1/proyecto` (fuera de `/opt/airflow`) y el
  paquete se importa vía `PYTHONPATH`, así un cambio de código no requiere reconstruir la
  imagen. `data/bandeja` se vuelve a montar encima con `:ro`, de modo que el sistema de
  origen es de solo lectura incluso para código que lo intente escribir por error.

## Pendientes

- Cadencia de pedido y horizonte del pronóstico.
- Costos de quiebre y merma (supuestos de negocio).
- Diseño de la bandeja: frecuencia y latencia por fuente, formato de correcciones.
- Disponibilidad de la información en el tiempo (fecha de llegada vs. fecha del dato).
- Reglas de validación: qué detiene el flujo y qué se tolera.
- Manejo de correcciones y reprocesamiento.
- Baseline, modelo de ML, esquema de validación temporal y criterio de promoción.
