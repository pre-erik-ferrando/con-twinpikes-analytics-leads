# Decisiones

Dónde y por qué el código se aparta de la definición v3.3 y de la revisión técnica de la v3.2, y lo que queda
pendiente de TWP o Fluidra. Las marcas son las del Word: [PROPUESTA] valor nuestro que TWP valida,
[PENDIENTE TWP] dato que TWP enviará, [PENDIENTE] decisión abierta.

## Desviaciones de la definición

| Tema | La definición dice | El código hace | Por qué |
|---|---|---|---|
| Reparto Python / dbt (DT.1.6) | Tres pasos en Python; normalización, filtro, cruce y puntuación en dbt | Python: normalización, identidad, cruce con clientes, rastreo y modelo. dbt: el filtro (`lgn_f_rule_evaluation`), la puntuación (`lgn_f2_score`), las exclusiones y los estados de Salesforce, cada uno con una sola implementación, que el motor lee. La puntuación se congela al exportar (`export_lead_out`). Sin las vistas, `precheck`, `enrich` y `export` se detienen y piden `python leadgen.py dbt`. Si se cumplen varias categorías o reglas de título, se informa la primera por orden alfabético. `run_db_tests.sh` compara cada vista con una copia en Python que solo usan las pruebas (`tests/sql_reference.py`) | Libphonenumber, la similitud de nombres y la identidad estable no existen en el SQL de ClickHouse. El filtro y la puntuación son reglas sobre `biz_cfg_*` que TWP irá ajustando: en SQL se leen junto a los demás modelos, y aplicar un cambio de reglas es volver a lanzar dbt. Decidido el 19-11-2026, aplicado el 23-11-2026 |
| Tablas (DT.2) | Por fichero subido, una tabla de fichero y otra de filas; `gmaps_run`, `landing_meta`, `suppression_list`; una tabla por lista de configuración | 47 tablas (eran 64). Un solo par `upload_file` / `upload_row` con `kind`; la llamada a Apify son columnas de `search_query_log`; alias y posibles duplicados en `business_identity_out`; las cinco listas simples de configuración en `biz_cfg_word_list` (`list_name`). Lo que es una regla sobre otras tablas (exclusiones, estados de Salesforce, categorías sin clasificar) es una vista, no una tabla | Tablas con la misma forma, o que nada leía, son más tablas sin más información. Intelek no leía ninguna de las quitadas. Migraciones del 23-11-2026 en `dwh/governance/migrations/` |
| Exclusiones (DT.8.1, DT.8.4) | Una exclusión es permanente | Salesforce: un lead DISCARDED en algún mes queda excluido para siempre, aunque un mes posterior diga otra cosa. Sales Ops: cuenta su decisión vigente; si corrige un EXCLUDED, el lead deja de estar excluido. `apply-sf-status` ya solo registra los «ya cliente» como corrección del cruce; la exclusión se ve en cuanto se carga el fichero | Antes se copiaban a `exclusion_out` al sincronizar, y una corrección de Sales Ops no se podía deshacer. Ahora se leen de su origen |
| Zona de búsqueda en Apify (DT.3.2) | Campos estructurados (`countryCode`, `state`, `county`, `city`), nunca texto libre | Se envía el contorno oficial de la zona (`customGeolocation`), simplificado a unos 100 m (`apify_outline_tolerance_deg`, [PROPUESTA]); un Land es la unión de sus distritos. Sin contorno cargado, la captación falla antes de gastar | Con los campos por nombre, el actor (versiones 0.14.792 y 0.14.793) descarta todas las fichas por fuera de zona y devuelve 0; con el contorno devuelve las de Graz (pruebas del 18-11-2026, `exploration/findings.md`). Sigue sin texto libre. Avisar a TWP |
| Importación automática (paso 0, DT.1.6) | El paso de carga lee solo la carpeta compartida del SharePoint: ficheros de clientes y fichero de estados de Salesforce | Por ahora por comando: `load-customers` y `load-sf-status`. El fichero de estados ya va a sus tablas (`upload_file`, `upload_row` → `lgn_d_sf_status`) con sus efectos | La carpeta, sus permisos y cada cuánto se lee están [PENDIENTE] en la propia definición; el Insight Generator, que cita como modelo, no tiene esa importación |
| Captación y enriquecimiento (DT.9) | La parte II describe una sola ejecución con fases A y B | Dos ejecuciones separadas (CAPTURE y ENRICH), como las pantallas de la parte I | El enriquecimiento va al modelo en modo BATCH, más barato, y puede tardar horas; la captación no tiene que esperarlo |
| Cruce espacial (DT.1.2) | Diccionario polygon de ClickHouse | Punto en polígono en Python (`geo.py`) contra los GeoJSON oficiales de `reference_geo` | Mismo resultado sin depender de un diccionario que hay que pedir al administrador; probado sin base de datos |
| Tablas creadas en un hook de dbt (DT.2.1) | `CREATE TABLE IF NOT EXISTS` en el arranque de dbt | `leadgen.py init` (desde `ddl.py`), y dbt las declara como fuentes | El motor escribe en landing y ops antes de que dbt corra; un único contrato de tablas genera DDL, vistas `_current` y fuentes de dbt |
| Instantánea de configuración en stg (DT.2.6) | stg resuelve la configuración con `snapshot_id` | El motor resuelve la configuración en Python a la fecha de la instantánea; las vistas `lgn_a_*` dan la configuración vigente para las vistas de consumo | La decisión la toma el motor; stg queda para leer |
| Tipo nullable de baja cardinalidad (anexo A) | `Nullable(LowCardinality(String))` | `LowCardinality(Nullable(String))` | ClickHouse solo admite ese anidamiento |
| Temperatura 0 y sin razonamiento extendido (DT.10.1) | Temperatura 0; sin razonamiento | Sin temperatura; `effort = low` | Los modelos actuales rechazan los parámetros de muestreo y no permiten desactivar el razonamiento; `llm_max_tokens_out` (2.000) tiene que dejarle sitio: vigilar las respuestas cortadas en el piloto |
| Modelo (DT.10.1, [PENDIENTE]) | Configurable, Anthropic u OpenAI | Los dos implementados (`llm_provider`). Elegido OpenAI, `gpt-6.2-sol` (gama media: 2 USD / 10 USD por millón de tokens de entrada / salida, la mitad en lote), con esfuerzo `low` y salida JSON estricta; las respuestas no se guardan en OpenAI (`store: false`) | Decisión del 20-11-2026 [PROPUESTA]: la clave disponible es de OpenAI. Cambiar de proveedor o de modelo es configuración (`llm_provider`, `llm_model`, `llm_price_*`) |
| Pantallas de Intelek (DF.13) | Lanzar, validar, exportar y cargar ficheros desde la DataApp | Vistas `lgn_v_*` listas; el lanzamiento y la carga de ficheros por línea de comandos | Revisión DT.9: spike con Intelek antes de comprometer pantallas |
| Prefijos de fecha | Estilo A: pk_, des_, imp_, val_ | Las fechas de las vistas llevan `dt_` | Convención del repositorio de TWP (`dt_target_month`); [PENDIENTE] confirmarlo (DT.1.1.3) |
| Motivo legible del cruce (DT.5.7) | Ejemplo en castellano | En inglés ("VAT matches customer 12345 (subsidiary AT), …") | La DataApp y Salesforce de Central Europe están en inglés |

## Decisiones de la revisión técnica aplicadas

- "Lote de captación" es `capture_batch`; "lote" del proveedor es el envío en modo BATCH (`llm_batch`).
- El Excel de inputs es la entrada versionada de cada carga (`seed_version`); después manda Intelek. Una
  semilla nunca pisa una clave editada; pisar exige `--overwrite` y queda en `ops.seed_load`.
- Antigüedad del extracto de clientes: bloqueo por encima de 7 días (definición v3.3, DT.5) salvo aprobación
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
| Reglas del filtro: categorías sin clasificar (`Metallbauunternehmen`, `Hersteller`), fichas sin categoría, «Therme» como regla HARD, tiendas de whirlpools (preguntado el 24-11-2026) | TWP | Negocios de piscina que el filtro aparta hoy | Las reglas del Excel v4, sin cambios |
| Reglas, términos y tipología de Pool & Spa, HVAC, Mass market y Pond | TWP | Activar esos perfiles | Inactivos en `biz_cfg_profile` |
| Tabla subregión → comercial | Fluidra | Columna Owner | Propietario por defecto por país |
| Fichero de estados de Salesforce: cabeceras y valores exactos (`SF_STATUS_IN`, `SF_REASON_IN` [PROPUESTA]) y quién lo deja cada mes | Fluidra, quien lleve el Salesforce nuevo | Lectura correcta del fichero | Columnas y valores de la pestaña Salesforce status file del Excel v4 |
| Valores de Salesforce: Lead Status, país, provincia, correo obligatorio | Fluidra | Formato final del fichero | `SF_LEAD_STATUS` NEW → New, `SF_COUNTRY` en inglés |
| Extractos de clientes de BE y NL; de AT y CH, uno más reciente (los recibidos tienen más de 7 días) | Fluidra | Cruce en BE/NL; captar en AT y CH sin `--approve-stale-extract` | BE/NL: cruce omitido con aviso. AT, CH y DE: listados de TWP (`TWP_SHEET_*`) recibidos el 19-11 |
| Build del actor de Apify y prueba de idioma | Prenomics | Fijar `apify_actor_build`, `apify_language` | `0.14.793` [PROPUESTA], la build contra la que se comprobaron los campos de entrada; idioma por subregión |
| Comprobación VIES / UID | Prenomics | `des_vat_id` en `lgn_v_lead` (solo con VALID) | `vat_check_status = NOT_CHECKED` |
| Identificadores de las sociedades de Fluidra | Fluidra | Señal FLUIDRA_ENTITY | `fluidra_entity_ids` vacío |
| Conjunto de control (`control_set.csv`) de Alemania (BSW) y Suiza | Prenomics | Medir DF.12 en DE y CH | Austria cargada: 47 socios del ÖVS con web o teléfono; «debe excluir» los que excluyen las reglas de nombre de TWP |
| Capacidad de Intelek: escritura en ClickHouse, botón que lanza procesos, descarga de ficheros, mapas, usuario de conexión (preguntas redactadas, por enviar) | Intelek | Pantallas | Línea de comandos |
| Base legal del tratamiento | Asesor legal de Fluidra | Arranque en producción | — |

## Diferencias con la definición, por decidir más adelante

El código sigue aquí la revisión técnica y no la definición v3.3. No se cambia hasta que haga falta.

- Aprobación de coste o de extracto antiguo: la definición deja la ejecución esperando en NEEDS_APPROVAL; la captación falla y se relanza con `--approve-cost` o `--approve-stale-extract`.
- País sin extracto de clientes: la definición lo bloquea salvo aprobación; el código sigue sin cruce, con aviso.
- Published: la definición lo da al exportar; el código, al confirmar la carga (`confirm-export --status UPLOADED`). Falta saber qué entiende TWP por Published.
- Recruce mensual: la definición descualifica también los leads publicados que resultan ser clientes; el código solo los lista en `lgn_v_published_customer_conflict`.
