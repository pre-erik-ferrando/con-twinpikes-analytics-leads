# Decisiones

Dónde y por qué el código se aparta de la definición v3.2 y de su revisión técnica, y lo que queda
pendiente de TWP o Fluidra. Las marcas son las del Word: [PROPUESTA] valor nuestro que TWP valida,
[PENDIENTE TWP] dato que TWP enviará, [PENDIENTE] decisión abierta.

## Desviaciones de la definición

| Tema | La definición dice | El código hace | Por qué |
|---|---|---|---|
| Reparto Python / dbt (DT.1.6) | Tres pasos en Python; normalización, filtro, cruce y puntuación en dbt | El motor en Python decide (normalización, identidad, filtro, cruce, puntuación) y escribe tablas `<paso>_out`; dbt son vistas que leen esos resultados para Intelek | libphonenumber, la similitud de nombres (token_sort_ratio) y la identidad estable no existen en SQL de ClickHouse; así una sola implementación sirve para captación, reproceso y recruce, y se prueba sin base de datos |
| Zona de búsqueda en Apify (DT.3.2) | Campos estructurados (`countryCode`, `state`, `county`, `city`), nunca texto libre | Se envía el contorno oficial de la zona (`customGeolocation`), simplificado a unos 100 m (`apify_outline_tolerance_deg`, [PROPUESTA]); un Land es la unión de sus distritos. Sin contorno cargado, la captación falla antes de gastar | Con los campos por nombre, el actor (versiones 0.14.792 y 0.14.793) descarta todas las fichas por fuera de zona y devuelve 0; con el contorno devuelve las de Graz (pruebas del 18-11-2026, `exploration/findings.md`). Sigue sin texto libre. Avisar a TWP |
| Cruce espacial (DT.1.2) | Diccionario polygon de ClickHouse | Punto en polígono en Python (`geo.py`) contra los GeoJSON oficiales de `reference_geo` | Mismo resultado sin depender de un diccionario que hay que pedir al administrador; probado sin base de datos |
| Tablas creadas en un hook de dbt (DT.2.1) | `CREATE TABLE IF NOT EXISTS` en el arranque de dbt | `leadgen.py init` (desde `ddl.py`), y dbt las declara como fuentes | El motor escribe en landing y ops antes de que dbt corra; un único contrato de tablas genera DDL, vistas `_current` y fuentes de dbt |
| Instantánea de configuración en stg (DT.2.6) | stg resuelve la configuración con `snapshot_id` | El motor resuelve la configuración en Python a la fecha de la instantánea; las vistas `lgn_a_*` dan la configuración vigente para las vistas de consumo | La decisión la toma el motor; stg queda para leer |
| Tipo nullable de baja cardinalidad (anexo A) | `Nullable(LowCardinality(String))` | `LowCardinality(Nullable(String))` | ClickHouse solo admite ese anidamiento |
| Temperatura 0 y sin razonamiento extendido (DT.10.1) | Temperatura 0; sin razonamiento | Sin temperatura; `effort = low` | Los modelos actuales rechazan los parámetros de muestreo y no permiten desactivar el razonamiento; `llm_max_tokens_out` (2.000) tiene que dejarle sitio: vigilar las respuestas cortadas en el piloto |
| Modelo (DT.10.1, [PENDIENTE]) | Configurable, Anthropic u OpenAI | Anthropic implementado (`claude-opus-5-5` por defecto en `biz_cfg_run_parameter`); OpenAI no | Una sola integración mientras no se decida el modelo |
| Pantallas de Intelek (DF.13) | Lanzar, validar, exportar y cargar ficheros desde la DataApp | Vistas `lgn_v_*` listas; el lanzamiento y la carga de ficheros por línea de comandos | Revisión DT.9: spike con Intelek antes de comprometer pantallas |
| Prefijos de fecha | Estilo A: pk_, des_, imp_, val_ | Las fechas de las vistas llevan `dt_` | Convención del repositorio de TWP (`dt_target_month`); [PENDIENTE] confirmarlo (DT.1.1.3) |
| Motivo legible del cruce (DT.5.7) | Ejemplo en castellano | En inglés ("VAT matches customer 12345 (subsidiary AT), …") | La DataApp y Salesforce de Central Europe están en inglés |

## Decisiones de la revisión técnica aplicadas

- "Lote de captación" es `capture_batch`; "lote" del proveedor es el envío en modo BATCH (`llm_batch`).
- El Excel de inputs es la entrada versionada de cada carga (`seed_version`); después manda Intelek. Una
  semilla nunca pisa una clave editada; pisar exige `--overwrite` y queda en `ops.seed_load`.
- Antigüedad del extracto de clientes: bloqueo por encima de 7 días (definición v3.2, DT.5) salvo aprobación
  `STALE_CUSTOMER_EXTRACT`, que se da al lanzar la captación (`capture --approve-stale-extract`) porque el
  identificador de la ejecución nace en ese momento. Un país sin extracto omite el cruce con aviso.
- Validación con alcance explícito: `approve-rest` muestra el recuento y exige `--yes`; un aprobado
  vuelve a To review con `reopen` mientras no se exporta (o su exportación se confirmó FAILED).
- Un lead que pasa a revisión sin el modelo (sin web y sin candidatos de cliente) también se puntúa, para
  que aparezca en la cola de validación.
- La captación estima el coste de Apify antes de llamar; por encima del umbral exige `--approve-cost` al
  lanzarla (como el extracto antiguo, porque la ejecución nace en ese momento).
- Un motivo de rechazo solo se acepta si aplica al perfil del lead (`applies_to_profiles`).
- La estimación del coste del modelo es la misma en la captación y en el enriquecimiento (`cost.enrich_estimate`).
- Una exportación se confirma una sola vez (UPLOADED o FAILED); cambiarla después podría duplicar leads en
  Salesforce.
- Exportación con `export_id` y estado; Published solo tras UPLOADED.
- El destino COCKPIT y `lgn_v_cockpit_feed` no se construyen; el cockpit carga el fichero de Salesforce.
- `max_leads` obligatorio en cualquier perfil distinto de Pool specialist.
- Propietario por defecto por país (`subregion_code` nulo en `biz_cfg_sales_owner`); sin él, la
  exportación se bloquea con la lista de leads sin Owner.

## Pendiente

| Qué | De quién | Qué bloquea | Valor provisional en el código |
|---|---|---|---|
| Instantánea congelada del Excel de inputs v4 | TWP | Primera carga de configuración | El cargador espera las cabeceras descritas en el Word; si faltan, falla con la lista de cabeceras encontradas |
| Reglas, términos y tipología de Pool & Spa, HVAC, Mass market y Pond | TWP | Activar esos perfiles | Inactivos en `biz_cfg_profile` |
| Tabla subregión → comercial | Fluidra | Columna Owner | Propietario por defecto por país |
| Valores de Salesforce: Lead Status, país, provincia, correo obligatorio | Fluidra | Formato final del fichero | `SF_LEAD_STATUS` NEW → New, `SF_COUNTRY` en inglés |
| Extractos de clientes de BE y NL; extracto en bruto de AT y CH | Fluidra | Cruce en BE/NL | Cruce omitido con aviso; pestañas de TWP (`TWP_SHEET_AT/CH`) |
| Build del actor de Apify y prueba de idioma | Prenomics | Fijar `apify_actor_build`, `apify_language` | `0.14.793` [PROPUESTA], la build contra la que se comprobaron los campos de entrada; idioma por subregión |
| Comprobación VIES / UID | Prenomics | `des_vat_id` en `lgn_v_lead` (solo con VALID) | `vat_check_status = NOT_CHECKED` |
| Identificadores de las sociedades de Fluidra | Fluidra | Señal FLUIDRA_ENTITY | `fluidra_entity_ids` vacío |
| Conjunto de control (`control_set.csv`) | TWP / Prenomics | Medir DF.12 | `lgn_v_coverage` vacía |
| Capacidad de Intelek: escritura, ficheros, botón | Intelek | Pantallas | Línea de comandos |
| Base legal del tratamiento | Asesor legal de Fluidra | Arranque en producción | — |
