# Proyecto Lead Generator

El **Lead Generator DACH**, proyecto conjunto de Prenomics y **TWP** para Fluidra Central Europe:
encontrar empresas de piscinas en Alemania, Austria y Suiza (y, más adelante, Bélgica y Países Bajos)
que todavía no son clientes de Fluidra y entregarlas a Salesforce, puntuadas, para que Inside Sales las
llame por orden de interés. Es un proyecto distinto del Insight Generator, con su propio repositorio y
sus propias bases de datos (`fld_*lgn`). Qué hace la herramienta y cómo está construida:
[Lead Generator](lead_generator.md).

## Quién hace qué

| Quién | Qué hace |
|---|---|
| **Fluidra** | Envía los extractos de clientes de cada país, recibe los leads en Salesforce y valida la definición |
| **TWP** | Define el modelo de perfil y de mercado: zonas y cuadrantes, perfiles y términos, exclusiones, puntuación y marcas (Excel de inputs) |
| **Prenomics** | Construye y opera el motor y la base de datos, y la configuración avanzada |
| **Intelek** | La DataApp: pantallas de captación, enriquecimiento, validación, exportación y configuración |

## Docs relevantes

- **Código**: el repositorio `con-twinpikes-analytics-leads`, con el mismo stack y las mismas
  convenciones que el Insight Generator (`con-twinpikes-analytics`).
- **Definición**: el Word v3.3 y la revisión técnica de la v3.2, en [`docs/spec/`](../spec); el Excel de
  inputs, en el SharePoint de TWP (fuera del repositorio).
- **Qué se aparta de la definición y qué está pendiente**: [Decisiones](decisiones.md).

## Alcance de esta entrega

La que propone la revisión técnica ("Qué construiría ahora"):

- **Pool specialist** en **Alemania, Austria y Suiza**, con el modelo de datos preparado para cinco
  países y cinco perfiles (el perfil y el país son columnas en toda la configuración).
- Captación y enriquecimiento separados, con aprobación de coste, modo BATCH o DIRECT y reanudación.
- Validación por rechazo con alcance explícito y auditoría; deshacer antes de exportar.
- Exportación con identificador y estado (GENERATED, UPLOADED, FAILED).
- Configuración en tablas `biz_cfg_*` editables, con carga desde el Excel de inputs y semillas.
- Cargador del fichero de estados de Salesforce y su efecto (exclusiones permanentes, fallo del cruce).
- Recruce mensual con extractos nuevos y vista de conflictos de leads ya publicados.
- Listado propio (Import list) con la definición mínima de la revisión [PROPUESTA].
- Vistas de consumo para las cinco pantallas y los tres mapas.

Queda fuera hasta tener datos de TWP o Fluidra: los otros cuatro perfiles (sin reglas ni bloque de
prompt, la comprobación previa los bloquea), Bélgica y Países Bajos (sin piloto de Apify, extractos ni
palabras de piscina), el aviso por correo y la escritura desde Intelek.
Detalle y motivo de cada desviación: [Decisiones](decisiones.md).

## Cómo sabremos que funciona (DF.12)

| Criterio | Dónde se mide |
|---|---|
| Cobertura del conjunto de control | `lgn_v_coverage` (con `biz_cfg_control_set` cargado) |
| Misma puntuación que el Excel de Austria de TWP | `lgn_f2_score_component` frente al Excel; test con el ejemplo [10, 7.5, 7.5, 2.5, 5, 0.5] = 33 (`run_db_tests.sh`) |
| Prácticamente todos los leads con zona | `lgn_v_lead.des_quadrant` no nulo |
| Los tres clientes de Austria apartados por el motor | `lgn_v_lead_discarded`, etapa CUSTOMER_MATCH |
| Trazabilidad de cada dato | `lgn_d_enrichment.sources_json` (página de origen de cada dato) |
| Exportación cargable sin retoques | prueba de carga con Fluidra del primer fichero |
