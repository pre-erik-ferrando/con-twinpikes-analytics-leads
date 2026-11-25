# Operación

Todo se lanza con `python leadgen.py <comando>` (la cabecera de [`leadgen.py`](../../leadgen.py) los
lista). Necesita la VPN y el `.env`.

## Cada semana, antes del día 1 de llamadas

1. **Captación**, con las zonas y el perfil del One2One:
   `capture --country AT --areas AT-501,AT-502 --terms Poolbau,Poolshop [--max-places 50] [--subdivide]`.
   Cada zona se envía a Apify como su contorno oficial (cargado con `load-geo`); una zona sin contorno
   hace fallar la captación antes de gastar. Devuelve la tanda (`cb_…`), encontrados, excluidos y la estimación del enriquecimiento. Si falla por
   la antigüedad del extracto de clientes, cargar uno nuevo o repetir la captación con
   `--approve-stale-extract` (queda registrado como aprobación `STALE_CUSTOMER_EXTRACT` de quien la lanza).
   Antes de llamar a Apify se estima su coste (consultas × tope × precio por ficha). Si supera
   `cost_approval_threshold_usd`, la captación no empieza: hay que repetirla con `--approve-cost USD`
   (al menos la estimación) o con menos zonas, términos o `--max-places`. Las dos aprobaciones valen igual en `import-list`.
2. **Categorías nuevas**: revisar `lgn_v_category_pending` y clasificarlas en `biz_cfg_category_rule`;
   cuentan desde la siguiente ejecución (o `reprocess`, gratis).
3. **Enriquecimiento**: `enrich --capture-batch cb_… [--mode AUTO|BATCH|DIRECT] [--max-leads N]`.
   - Por encima de 10 USD queda en `AWAITING_COST_APPROVAL`: `approve-cost RUN --amount 25` y `resume RUN`.
   - En modo BATCH queda en `AWAITING_BATCHES`: `resume RUN` hasta que el proveedor termine (minutos u
     horas, hasta 24).
4. **Validación** en Intelek (`lgn_v_validation_queue`) o por línea de comandos: `review`, `reject
   --business-id N --reason KA_CHAIN`, y `approve-rest --capture-batch cb_… --yes` para aprobar el resto.
5. **Exportación**: `export --capture-batch cb_… --out exports/` genera
   `<país>_salesforce_upload_<fecha>.xlsx`. Tras subirlo al cockpit (o a Salesforce, cuando lo sustituya):
   `confirm-export exp_… --status UPLOADED`; si la carga falla, `--status FAILED` y se puede regenerar.
   Una exportación se confirma una sola vez: después no se puede cambiar de UPLOADED a FAILED ni al revés.

## Cada mes: estado en Salesforce

No se cargan resultados de llamada (definición v3.3, DF.10). Del lead solo vuelve su estado, en el
fichero de estados mensual de Salesforce. Mientras el paso 0 no lea solo la carpeta compartida:
`load-sf-status lgn_sf_status_AAAAMM.csv`. Los descartados quedan excluidos para siempre (con motivo «ya
cliente», cuentan además como fallo del cruce); convertidos, en curso y en seguimiento siguen en
Published. Avisa de los lead codes que la herramienta no exportó (no se cargan) y de las zonas que no
coinciden con la del lead.

## Cada mes y antes de abrir una zona nueva

`load-customers EXTRACTO --country AT --source-system TWP_SHEET_AT --extract-date AAAA-MM-DD` y después
`rematch --country AT`: los leads encontrados, por revisar o aprobados que ya son clientes pasan a
Excluded by engine; los publicados aparecen en `lgn_v_published_customer_conflict`. Revisa los leads de
todos los perfiles a la vez, porque el cruce con clientes es por empresa, no por perfil.

## Cuando cambia la configuración

`reprocess --country AT [--capture-batch cb_…]` vuelve a cruzar con clientes lo que ahora pasa el filtro (el filtro
y la puntuación ya son en vivo, en SQL) sin captar, rastrear ni llamar
al modelo. Un cambio de puntos o de umbrales no necesita nada: la puntuación se calcula en vivo en SQL
(`lgn_f2_score`). Los leads ya exportados conservan la puntuación que tenían al exportarse.
Tras cambiar un modelo dbt (por ejemplo, la puntuación), `python leadgen.py dbt`: sin sus vistas,
`precheck`, `enrich` y `export` se detienen y lo piden.

Lo que `reprocess` deja dentro por primera vez (por ejemplo, tras pasar una categoría a INCLUDE) no tiene
web ni modelo: `enrich --capture-batch cb_…` lo enriquece. Un lead ya enriquecido no vuelve al modelo: tras
cambiar el prompt, `enrich --capture-batch cb_… --reread` (cuesta lo mismo que enriquecer la tanda otra vez).

## Seguimiento

`status [RUN]` en la terminal, o `lgn_v_run_summary` (estado, mensaje, coste estimado y real).

Para ver por qué cada empresa acabó como acabó: `python exploration/run_trace.py cb_…` (solo lee). Deja
en `traces/<cb>/` las filas de cada paso y un `trace.md` con una sección por empresa: ficha de Google,
filtro, cliente de Fluidra según Google, rastreo de la web, cliente según la web, modelo y puntuación.
`traces/` no se versiona porque tiene datos de clientes.
