# Implementación v11.1 · equivalencias de localidades y variante Colectora

## Problema corregido

La línea de Empresa Sarmiento `CÓRDOBA - MALAGUEÑO - CARLOS PAZ` aparece en
la planilla orientativa como `x Colectora`, pero los PDF semanales dejan vacía
la columna de ruta en 102 servicios. El importador no los vinculaba por su
criterio conservador, por lo que sus paradas intermedias no aparecían como
origen o destino.

`San Nicolas` y `San Nicolás` ya se normalizaban como el mismo nombre, y
`Carlos Paz` ya se reconocía como equivalente a `Villa Carlos Paz`. La falla
no era la tilde sino la ausencia de la palabra `Colectora` en el PDF.

## Criterio aplicado

Se incorpora una equivalencia explícita y limitada a:

- corredor: Punilla;
- empresa/CUIT: Empresa Sarmiento S.R.L. / 30-70730781-8;
- línea PDF: `CÓRDOBA - MALAGUEÑO - CARLOS PAZ`;
- línea orientativa: `CÓRDOBA - MALAGUEÑO - CARLOS PAZ x Colectora`;
- ruta PDF: únicamente cuando el campo está vacío.

No se realiza ninguna vinculación por semejanza. Las 13 salidas cuya ruta dice
`NO INGRESA A SAN NICOLÁS` permanecen sin esa parada y no se ofrecen para
viajar hacia o desde San Nicolás.

## Resultado

- 5.504 servicios vigentes; sin cambios en horarios.
- Servicios con recorrido intermedio vinculado: 4.342 (antes 4.240).
- Punilla: 1.062 servicios vinculados (antes 960).
- 51 servicios de ida y 51 de vuelta incorporan el recorrido por Colectora.
- 451 perfiles vinculados y 324 aptos para trazado vial gradual.
- Se conservan 520 puntos geolocalizados y 111 paradas vinculadas pendientes.
- Se conservan los 8 trazados piloto, sin nuevas consultas al enrutador.
- No se modifican el historial, los PDF ni la automatización semanal.

## Controles incorporados

- igualdad entre `San Nicolas` y `San Nicolás`;
- presencia de la línea Sarmiento al consultar Córdoba → San Nicolás;
- exclusión de servicios con `NO INGRESA A SAN NICOLÁS`;
- restricción de la equivalencia al CUIT, corredor, línea y variante conocidos;
- conservación de los 5.504 servicios publicados.

La versión completa supera 74 pruebas: 22 de Python y 52 de JavaScript.
