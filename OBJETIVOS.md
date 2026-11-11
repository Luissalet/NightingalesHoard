# Objetivos del compilador de libros

Corrección 2006 revisada e integrada: sistemas de fechas1900/1904, texto literal y verificación vacía. El resto de objetivos de Nightingale se conserva en su documentación de producto.

Ampliar funciones y tipos de fórmula cuando haya un encargo y una referencia independiente para contrastarlos. Mantener los resultados fuera del subconjunto como congelados explícitos. La exportación debe seguir ejecutándose con la biblioteca estándar, conservando el cálculo y el sistema de fechas del libro.

2011 implementa la exportación de una calculadora web local autónoma: formulario y resultados en un archivo Python, con fuentes y licencias incorporadas. Revisión 2 aprobada e integrada sobre la corrección 2006. No exige infraestructura adicional al cliente; un ejecutable sin Python no forma parte de esta entrega.

## Registro de eventos

Entregado el corte vertical de perfil, calibración con periodo reservado y receta. Cerrado: la propuesta de modelo se edita en la pestaña Calibración (distribución de servicio, proporciones de ruta, capacidad y tasa de llegadas, con procedencia y valor ajustado conservados; la validación y las alternativas se repiten con las mismas semillas y se muestra la diferencia con el modelo ajustado); el emparejamiento inicio/fin usa el recurso cuando está asignado (si no, orden de llegada) e informa de la regla usada por actividad; las referencias de fila salen como «fila N» en español; una columna de tiempo ya convertida a instante con zona se detecta y se indica la reimportación con `text_columns`. Pendiente para ampliar: llegadas con estacionalidad horaria en la calibración y edición de la distribución empírica valor a valor (hoy se sustituye por una distribución paramétrica).

Entregado el corte vertical de perfil, calibración con periodo reservado y receta. Pendiente para ampliar: llegadas con estacionalidad horaria en la calibración, emparejamiento de ciclo de vida por recurso cuando hay instancias solapadas de la misma actividad, y edición de la propuesta de modelo antes de simularla (hoy se revisa y se guarda en Escenarios para editarla allí).

## Presupuestos de fabricación por escenarios

Entregado el corte vertical (`nightingale/fabquote.py`, `data_fabquote`, página Presupuestos de fabricación): parámetros con unidades y fuentes, partidas por categoría, plantillas FDM, CNC/láser y hormigón, escenarios, sensibilidad, reales frente a presupuesto y exportación. Pendiente para ampliar: calibración automática de tarifas y rendimientos a partir de varios trabajos reales (hoy solo se compara y se sugiere la razón real/presupuesto), lectura de la trayectoria desde un archivo de segmentador o de trayectorias, partidas de superficies superiores e inferiores en FDM, y lectura del informe de inspección de Vulcan como avisos estructurados.

## Relato con los datos

Entregado el corte vertical (`nightingale/story.py`, `nightingale/story_export.py`, `data_story` como cinco herramientas `story_*`, página Relato): hallazgos de tendencia, comparación entre grupos, valores atípicos, concentración y cambio entre periodos, cada uno con su gráfico, su frase con la cifra exacta y la consulta que lo produce; el texto del modelo se descarta si cambia una cifra. Exportación a HTML autónomo y, por el Hub, a presentación en Cicero's Hoard con un hallazgo por diapositiva. Pendiente para ampliar: más clases de hallazgo (estacionalidad, correlación entre dos columnas, participación por subgrupo dentro de cada categoría), elección de la columna de tiempo y del grano desde la pantalla, y exportación a PDF con el mismo contenido que el HTML.
