# Nightingale — libros Excel ejecutables

Este apartado describe el compilador de libros; README y docs conservan el resto de capacidades del Hoard. Programa Hangar, ecosystem1937; corrección nightingale2006 sobre la base1949 integrada9e9f2d3. Candidato pendiente de revisión e integración.

Herramienta nativa `data_workbook`: compilar, listar, leer, calcular con parámetros nuevos, contrastar valores guardados, verificar casos nuevos conExcel/LibreOffice, trazar resultados y exportar un móduloPython autónomo. En el host normal se activa explícitamente el módulo `data`; descubrir MCP no arranca aplicaciones ni motores.

El sistema de fechas1900/1904 pertenece al libro, no al proceso global. Se conserva al cargar constantes/fechas, plegar fórmulas constantes, calcular, mostrar fechas, preparar recálculos y exportarPython. Los cálculos concurrentes no cambian el sistema de otro libro. El texto literal que empieza porigual no se ejecuta como fórmula. Verificar cero casos o cero resultados comparables es inconcluso, sin arrancarExcel ni afirmar acuerdo.

38pruebas del compilador positivas, incluyendoExcel real para ambos sistemas, cambios de parámetros, exportación y concurrencia. MCPstdio y navegador reales con datos inventados; sin modelos ni datos deLuis. PyYAML se declara como dependencia ya usada por el arranque normal. No amplía el subconjunto de fórmulas: lo no soportado se conserva congelado y se declara.

Revisión2: el selector web usa el sistema del libro para mostrar y editar fechas; los resúmenes antiguos se actualizan en la vista de lectura desde el original retenido, conservando sus bytes guardados.
