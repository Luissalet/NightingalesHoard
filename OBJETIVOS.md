# Objetivos del compilador de libros

Corrección 2006 revisada e integrada: sistemas de fechas1900/1904, texto literal y verificación vacía. El resto de objetivos de Nightingale se conserva en su documentación de producto.

Ampliar funciones y tipos de fórmula cuando haya un encargo y una referencia independiente para contrastarlos. Mantener los resultados fuera del subconjunto como congelados explícitos. La exportación debe seguir ejecutándose con la biblioteca estándar, conservando el cálculo y el sistema de fechas del libro.

2011 implementa la exportación de una calculadora web local autónoma: formulario y resultados en un archivo Python, con fuentes y licencias incorporadas. Revisión 2 aprobada e integrada sobre la corrección 2006. No exige infraestructura adicional al cliente; un ejecutable sin Python no forma parte de esta entrega.

## Registro de eventos

Entregado el corte vertical de perfil, calibración con periodo reservado y receta. Pendiente para ampliar: llegadas con estacionalidad horaria en la calibración, emparejamiento de ciclo de vida por recurso cuando hay instancias solapadas de la misma actividad, y edición de la propuesta de modelo antes de simularla (hoy se revisa y se guarda en Escenarios para editarla allí).

## Presupuestos de fabricación por escenarios

Entregado el corte vertical (`nightingale/fabquote.py`, `data_fabquote`, página Presupuestos de fabricación): parámetros con unidades y fuentes, partidas por categoría, plantillas FDM, CNC/láser y hormigón, escenarios, sensibilidad, reales frente a presupuesto y exportación. Pendiente para ampliar: calibración automática de tarifas y rendimientos a partir de varios trabajos reales (hoy solo se compara y se sugiere la razón real/presupuesto), lectura de la trayectoria desde un archivo de segmentador o de trayectorias, partidas de superficies superiores e inferiores en FDM, y lectura del informe de inspección de Vulcan como avisos estructurados.
