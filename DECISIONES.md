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

## D4 · Diseño de la bandeja (sistema de origen simulado)

- **Contexto:** el enunciado pide archivos que llegan por fecha y fuente, con frecuencias y
  anticipaciones distintas y con correcciones posteriores. En la evaluación, los docentes
  colocan sus propios CSV de Kaggle (con un periodo no publicado) y ejecutan sin
  intervención manual.
- **Organización por fecha de llegada**, no por fecha del dato
  (`<fuente>/llegada=AAAA-MM-DD/<fuente>_<clave>.csv`). *Alternativa descartada:* carpetas
  por fecha del dato con la llegada en metadatos. Con la llegada en la ruta, el principio
  "solo lo disponible al emitir" se reduce a filtrar carpetas con `llegada ≤ fecha lógica`,
  sin depender de fechas de modificación del sistema de archivos.
- **Latencias:** ventas D+1 (cierre de caja), transacciones D+2 (llegan de otro sistema),
  petróleo D+1 solo días hábiles (el mercado no cotiza fines de semana), promociones 14 días
  antes (se planifican), feriados el 15 del mes anterior (calendario publicado), tiendas en
  la carga inicial (maestro estable). `onpromotion` se separa de las ventas porque es la
  única variable de `train.csv` conocida a futuro.
- **Corte:** carga inicial = todo lo que llegó hasta el 2016-12-31 (4 años para entrenar);
  bandeja = desde el corte hasta la última fecha de `train.csv`, detectada al generar.
  Así un CSV con más fechas extiende la bandeja sin tocar código.
- **Correcciones:** una corrección es el mismo archivo en una carpeta de llegada posterior;
  la llegada más reciente es la válida y lo ya entregado nunca se reescribe. Dos orígenes:
  1. *Simuladas:* ≈ 2 % de los archivos de ventas llegan primero con errores (≈ 5 % de sus
     filas con ventas alteradas entre −40 % y +40 %) y la versión real llega 2–5 días
     después. Deterministas por semilla y fecha. Con los datos de Kaggle actuales: 10
     archivos (ver `_manifiesto.csv`, filas con `version = 2`).
  2. *Detectadas:* si un CSV de Kaggle cambia entre ejecuciones, las fechas cuyo contenido
     cambió se entregan como versión nueva 3 días después de su llegada original (o al día
     siguiente de la última versión, si es posterior). Las fechas nuevas se entregan con su
     latencia normal.
- **Generación automática e incremental:** un servicio `bandeja-init` del compose corre el
  generador antes de que arranque Airflow; es el único con escritura sobre la bandeja. Si
  la huella MD5 de los CSV no cambió, no hace nada (≈ 3 s). La carga inicial se entrega una
  sola vez: cambios en fechas anteriores al corte no se reenvían.
- **Huecos naturales** (25 de diciembre sin ventas, fines de semana sin petróleo, nulos del
  petróleo) se dejan tal cual: son situaciones esperadas que el flujo debe tolerar.

## Pendientes

- Cadencia de pedido y horizonte del pronóstico.
- Costos de quiebre y merma (supuestos de negocio).
- Reglas de validación: qué detiene el flujo y qué se tolera.
- Manejo de correcciones y reprocesamiento.
- Baseline, modelo de ML, esquema de validación temporal y criterio de promoción.
