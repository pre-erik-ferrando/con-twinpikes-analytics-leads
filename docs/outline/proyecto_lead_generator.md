# Proyecto Lead Generator

## Qué hace (DF.1)

Busca en Google Maps por perfil, término y zona; aparta lo que no es negocio de piscina y lo que ya es
cliente; lee la web de cada empresa, la puntúa con el modelo de perfil de TWP y deja que Sales Ops
rechace lo que no vale. Los aprobados se exportan a Salesforce antes de la llamada. Todo lo encontrado
se guarda: si cambian las reglas, se vuelve a filtrar y puntuar sin pagar otra vez la búsqueda ni la
lectura de las webs.

## El recorrido de un lead

| Paso | Comando | Resultado |
|---|---|---|
| Captación | `leadgen.py capture` | Una tanda de captación (`capture_batch`): encontrados, excluidos por el filtro y por ser cliente, estimación de coste del enriquecimiento |
| Enriquecimiento | `leadgen.py enrich` | Webs leídas, análisis del modelo comprobado, segundo cruce con clientes, puntuación y Rating |
| Validación | Intelek (o `review`, `reject`, `approve-rest`) | Rechazos con motivo; el resto aprobado con alcance explícito |
| Exportación | `leadgen.py export` + `confirm-export` | Fichero de Salesforce; Published al confirmar la subida |
| Resultados de llamada | `leadgen.py load-cockpit` | Los Not qualified no vuelven a salir; mapa 3 |
| Actualización mensual | `load-customers` + `rematch` | Los leads que ya son clientes se descualifican |

Estados del lead (DF.4), calculados en `lgn_v_lead.des_status`: Found, Excluded by engine, To review,
Rejected, Approved, Published.

## Alcance de esta entrega

La que propone la revisión técnica ("Qué construiría ahora"):

- **Pool specialist** en **Alemania, Austria y Suiza**, con el modelo de datos preparado para cinco
  países y cinco perfiles (el perfil y el país son columnas en toda la configuración).
- Captación y enriquecimiento separados, con aprobación de coste, modo BATCH o DIRECT y reanudación.
- Validación por rechazo con alcance explícito y auditoría; deshacer antes de exportar.
- Exportación con identificador y estado (GENERATED, UPLOADED, FAILED).
- Configuración en tablas `biz_cfg_*` editables, con carga desde el Excel de inputs y semillas.
- Cargador del CSV del cockpit y su efecto (exclusiones permanentes, fallo del cruce).
- Recruce mensual con extractos nuevos y vista de conflictos de leads ya publicados.
- Listado propio (Import list) con la definición mínima de la revisión [PROPUESTA].
- Vistas de consumo para las cinco pantallas y los tres mapas.

Queda fuera hasta tener datos de TWP o Fluidra: los otros cuatro perfiles (sin reglas ni bloque de
prompt, la comprobación previa los bloquea), Bélgica y Países Bajos (sin piloto de Apify, extractos ni
palabras de piscina), la comprobación VIES/UID del IVA, el aviso por correo y la escritura desde Intelek.
Detalle y motivo de cada desviación: [Decisiones](decisiones.md).

## Cómo sabremos que funciona (DF.12)

| Criterio | Dónde se mide |
|---|---|
| Cobertura del conjunto de control | `lgn_v_coverage` (con `biz_cfg_control_set` cargado) |
| Misma puntuación que el Excel de Austria de TWP | `lgn_f3_score_component` frente al Excel; test con el ejemplo [10, 7.5, 7.5, 2.5, 5, 0.5] = 33 |
| Prácticamente todos los leads con zona | `lgn_v_lead.des_quadrant` no nulo |
| Los tres clientes de Austria apartados por el motor | `lgn_v_lead_discarded`, etapa CUSTOMER_MATCH |
| Trazabilidad de cada dato | `lgn_d_enrichment.sources_json` (página de origen de cada dato) |
| Exportación cargable sin retoques | prueba de carga con Fluidra del primer fichero |
