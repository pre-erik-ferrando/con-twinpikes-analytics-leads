# Configuración

Toda la configuración vive en tablas `biz_cfg_*` (en `fld_bizlgn`) que TWP y negocio editan desde
Intelek. Reglas comunes (DT.2.1):

- Columnas en todas: `country` (nulo = todos los países), `active`, `updated_by`, `inserted_at`.
- Una fila de un país gana a la de país nulo con la misma clave; una de un perfil, a la de perfil nulo.
- Solo inserción: se edita insertando una fila nueva; se borra insertándola con `active = 0`. Intelek
  lee las vistas `biz_cfg_<nombre>_current`.

## De dónde salen los valores

| Fuente | Tablas | Cómo se carga |
|---|---|---|
| Excel de inputs de TWP (DF.14) | `search_area`, `strategic_zone` (Admin divisions), `search_term` (Profiles & keywords), `category_rule` (Google categories), `title_rule` (Name exclusions), `pool_signal` (Pool words), `brand` (Brands), `sales_owner` (Subregion → sales rep) | `leadgen.py load-config FICHERO.xlsx --version <v>` |
| Semillas del repositorio (valores del Word y [PROPUESTA]) | `profile`, `scoring_rule`, `rating_band`, `validation_reason`, `shared_domain`, `name_stopword`, `page_priority`, `matching_rule`, `source_column`, `value_map`, `category_of_interest`, `export_column`, `legal_form`, `run_parameter`, y unas pocas filas de `title_rule`, `pool_signal`, `brand` y `search_term` que cita el Word | `leadgen.py seed` ([`seeds/`](../../dwh/transf_py/leadgen/seeds)) |
| Conjunto de control (DF.12) | `control_set` | Semilla [PROPUESTA]: Austria, socios del ÖVS (directorio de la WKO, 20-11-2026). Alemania (BSW) y Suiza, pendientes |

Cada carga es una versión (`seed_version`) y queda en `ops.seed_load`. Solo inserta las claves que no
existen: nunca pisa lo que se ha cambiado en Intelek salvo con `--overwrite`.

## Comprobación previa

`leadgen.py precheck` (y cada ejecución al arrancar) bloquea si: el perfil no está activo o no tiene
términos, categorías ni reglas de título; no hay área o término; un área no existe o una subregión no
tiene idioma; un factor de puntuación no tiene todos sus tramos; los umbrales del Rating no crecen; un
valor está fuera de su dominio; una regla SUBSTRING tiene menos de seis letras. Avisa sin bloquear de
subregiones sin cuadrante, marcas sin grupo y países sin lista de marcas propia.

## Parámetros técnicos

`biz_cfg_run_parameter` (anexo B.22 con los cambios de la revisión): topes de gasto, límites del
rastreo, presupuesto de tokens, modelo, modos de envío, umbral de aprobación de coste (10 USD),
antigüedad del extracto (7 días, definición v3.3), referencia de reseñas provisional (34,8 y 10,5, Austria).
