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

## D5 · Ingesta y validación

- **Histórico particionado igual que la bandeja** (`historico/<fuente>/llegada=D/`), con
  `fecha_llegada` en cada fila. *Alternativa descartada:* una tabla consolidada que se
  actualiza en cada ingesta (upsert). Con particiones por llegada cada día es independiente
  (el backfill corre fechas en paralelo), reprocesar un día solo reemplaza su carpeta y la
  "vista a una fecha de corte" es una consulta: particiones con `llegada ≤ corte` y, por
  clave, la llegada más reciente. Así las correcciones se aplican solo desde el día en que
  llegan, y un pronóstico pasado ve exactamente lo que se sabía entonces.
- **Inválido vs. esperado** (reglas en `reposicion/validacion.py`):
  - *Detiene:* columnas faltantes, tipos no interpretables, nulos (salvo petróleo), fecha
    distinta a la del nombre del archivo, llaves duplicadas, negativos, tiendas o familias
    que no están en la carga inicial, archivo vacío.
  - *Avisa y continúa:* fuente sin archivos en el día (feriado, fin de semana, atraso),
    dato esperado según la latencia que aún no llega, petróleo faltante, tienda con ventas
    en cero (cerrada o sin abrir, p. ej. la tienda 52 antes de abril de 2017), combinaciones
    tienda × familia ausentes.
- **Granularidad del bloqueo: por archivo.** Un archivo inválido no entra al histórico y
  deja en rojo la tarea de su fuente (`AirflowFailException`, sin reintentos: un error de
  datos falla igual en cada intento); los archivos válidos del mismo día y las demás fuentes
  sí se ingieren. *Alternativa descartada:* detener todo el día, lo que bloquearía el
  pronóstico por un archivo de una sola fuente. Si llega una versión corregida, entra en su
  propia fecha de llegada sin intervención manual.
- **Carga inicial:** la ingiere la primera tarea de cada corrida y se omite si la huella de
  la entrega ya está registrada; sus tiendas y familias son la referencia de llaves válidas.
  Cada registro conserva su **fecha de llegada original** (columna `fecha_llegada` que trae
  la bandeja), no la del corte: así la vista a una fecha de 2016 —la que usan los
  backtests de los primeros pronósticos de 2017— sabe exactamente lo que se sabía entonces.
  Las tiendas son datos maestros y se consideran conocidas desde el inicio.
- **Reportes:** `historico/_reportes/llegada=D/<fuente>.json` con archivos, correcciones,
  inválidos y avisos de cada día; son la evidencia de cómo reaccionó el flujo.

## D6 · Cadencia, horizonte, costos y pedido

- **Cadencia semanal (lunes) y horizonte de 14 días.** La dirección pidió una sugerencia
  semanal; 14 días cubren la semana del pedido más un margen para el tiempo de entrega del
  centro de distribución, y coinciden con la anticipación con que se conocen las
  promociones (más allá de 14 días la promoción del día sería desconocida).
- **Costos: quiebre 3, merma 1 (por unidad).** Supuesto de negocio documentado: una unidad
  faltante pierde el margen y deteriora la lealtad del cliente; una sobrante se pierde solo
  en parte (no todas las familias son perecederas). La razón 3:1 da un cuantil crítico
  `Cu / (Cu + Co) = 0.75`.
- **Pedido = cuantil crítico diario (newsvendor).** El pedido sugerido de cada día es el
  pronóstico en el cuantil 0.75. *Simplificación consciente:* el total semanal se reporta
  como suma de los pedidos diarios; el cuantil de una suma no es la suma de cuantiles, pero
  el pedido diario es coherente con la métrica de negocio, que se evalúa día a día.
- Cambiar los costos en `params.yaml` cambia el cuantil crítico, el modelo que se entrena
  para él y el pedido, sin tocar código.

## D7 · Features sin fuga de información

- Toda feature de un origen O usa solo lo que había llegado en O según las latencias de la
  bandeja: ventas ≤ O-1, transacciones ≤ O-2, petróleo ≤ O-1, promociones de fechas ≤ O+14,
  feriados de meses ya publicados (lo no conocido queda como NaN, que LightGBM maneja).
- La regla se aplica también a los **orígenes de entrenamiento**: el modelo aprende con la
  misma información que tendrá al predecir. Pruebas que lo verifican: cambiar las ventas
  desde O en adelante no altera ninguna feature; promociones y feriados fuera de su
  ventana de publicación son desconocidos.
- Features: niveles y dispersión recientes (7/14/28/56 días), mismo día de la semana,
  fracción de ceros, promoción del día y reciente, feriados nacional/regional/local,
  eventos, días de pago (15 y fin de mes), transacciones de la tienda, petróleo, día de la
  semana y del mes, y atributos de la tienda.
- **Sin mes del año.** Con 16 orígenes semanales (≈ 4 meses) el modelo no puede aprender
  estacionalidad anual; peor aún, el pronóstico de enero entrenado con septiembre–diciembre
  solo había visto `mes = 1` en el 1 de enero (tiendas cerradas) y predecía ventas casi
  nulas para todo enero. Se detectó al revisar el primer pedido publicado (122 mil unidades
  contra 5.9 millones vendidas) y se eliminó la variable; el feriado del 1 de enero ya lo
  representa `feriado_nacional`. Quitarla no cambia el backtest de mayo–julio.
- **Tienda cerrada = día sin dato.** Si una tienda vende 0 en todas sus familias (1 de
  enero, cierres temporales, tiendas que aún no abren) el día se trata como faltante y no
  como demanda cero; si no, el pronóstico emitido el 2 de enero arrancaba de "ventas = 0".

## D8 · Modelos, validación y criterio de reemplazo

- **Baseline:** cuantil empírico de las últimas 8 ventas del mismo día de la semana. Sin
  entrenamiento, captura la estacionalidad semanal y da cuantiles directamente.
- **Modelo de ML: LightGBM global** (una sola red de árboles para las 1,782 series), con
  regresión cuantílica sobre `log1p(ventas)`, un modelo por cuantil (0.10, 0.50, 0.75,
  0.90); los cuantiles se ordenan al predecir para que no se crucen. Se entrena con 16
  orígenes semanales (≈ 400 mil filas, ≈ 20 s).
- **Deep learning: no.** Con ≈ 1,800 series y pocos años de historia, un modelo de árboles
  con buenas features suele igualar a redes recurrentes o transformers a una fracción del
  costo de cómputo y de mantenimiento; la ganancia esperada no justifica GPU ni la
  complejidad operativa en Airflow.
- **Validación temporal:** backtest con origen móvil, 4 cortes (lunes) separados 28 días,
  terminando en el último lunes con horizonte completo observado. En cada corte, cada
  modelo se entrena **solo con la vista de ese día** y se evalúa contra la verdad final
  (incluidas correcciones). Los cortes son los mismos para todos los modelos.
- **Métricas:** WAPE del P50, pinball loss relativa (cuantiles publicados), cobertura del
  intervalo P10–P90 (nominal 80 %) y **costo de negocio** del pedido con los costos de la
  configuración.
- **Criterio de reemplazo:** un modelo candidato reemplaza al vigente si, en el mismo
  backtest, reduce el costo de negocio total en al menos **3 %**
  (`promocion_modelo.mejora_minima_costo`). Se decide por costo y no por WAPE porque es lo
  que le importa a operaciones; el umbral evita cambiar de modelo por ruido.
- **Resultado** ([reportes/comparacion_modelos.md](reportes/comparacion_modelos.md)):
  LightGBM reduce el costo 11.5 % frente al baseline; WAPE 0.133 vs 0.144 y cobertura del
  intervalo 78 % vs 69 % (nominal 80 %). LightGBM pasa a ser el modelo vigente.

## D9 · Orquestación del pronóstico y registro de modelos

- **Un DAG semanal (lunes)** separado de la ingesta diaria: `esperar_ingesta` →
  `revisar_modelo` → `publicar`, con `max_active_runs=1` porque cada revisión de modelo
  depende de las anteriores.
- **Dependencia entre DAGs con un sensor propio** (modo `reschedule`) que verifica que
  existan los reportes de ingesta de todas las llegadas hasta la fecha lógica.
  *Alternativas descartadas:* `ExternalTaskSensor` (espera una tarea de una corrida
  concreta, no "todas las llegadas hasta D") y un único DAG diario con rama de lunes
  (un backfill paralelo podría pronosticar antes de ingerir días previos). Con el sensor,
  ambos backfills corren a la vez y el pronóstico nunca ve datos incompletos. Las fechas
  fuera del periodo con datos se omiten de inmediato.
- **Entrenar dentro del flujo.** El docente no puede descargar nuestro modelo (remoto DVC
  local), así que el modelo se entrena desde los datos al primer pronóstico y se revisa
  cada 4 semanas (`reentrenar_cada_semanas`): backtest de los modelos disponibles con la
  vista de esa fecha y criterio de reemplazo de D8 frente al vigente. El vigente inicial es
  el baseline. Entre revisiones se reutiliza el último modelo (≈ 2–3 min por revisión).
- **Registro de modelos por fecha** (`modelos/registro.json` + un `.joblib` por revisión):
  el vigente en C es la última revisión con fecha ≤ C. Reprocesar un lunes pasado usa el
  mismo modelo que entonces, sin importar el orden de ejecución; repetir una revisión no
  reentrena. LightGBM se entrena en modo determinista para que un reentrenamiento dé el
  mismo modelo.
- **Validación de salida:** antes de publicar, el total P50 de la primera semana se compara
  con lo vendido en los 7 días previos; fuera de 0.4–2.5 veces
  (`pronostico.validacion_salida`) la publicación se detiene con `AirflowFailException`.
  Un salto así es un problema del modelo o de los datos, no del negocio, y es mejor una
  celda roja que un pedido absurdo. Esta regla habría atrapado el error del mes (D7).
- **Publicación idempotente** en `publicados/fecha_corte=C/`; la metadata incluye el commit
  de Git (leído de `.git` sin depender del binario) y la revisión del modelo, que junto con
  la vista del histórico en C permiten reproducir el pronóstico.
- **Resultado de la operación 2017** (backfill completo, `modelos/registro.json`): la
  primera revisión (2-ene) elige LightGBM; en la del 24-abr el baseline lo supera en el
  backtest (12.87 M vs 13.81 M) y pasa a ser el vigente hasta el 14-ago, cuando LightGBM
  vuelve a ganar con un 11.5 % de mejora. El criterio de reemplazo no favorece al modelo más
  complejo: cambia solo cuando la evidencia del backtest lo justifica.
