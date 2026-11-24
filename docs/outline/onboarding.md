# Onboarding

## Qué leer, en orden

1. [Lead Generator](lead_generator.md) y [Proyecto Lead Generator](proyecto_lead_generator.md).
2. La definición v3.3, parte I (funcional), en [`docs/spec/`](../spec); la parte II y la revisión técnica
   cuando se vaya a tocar código.
3. [Arquitectura](arquitectura.md), [Modelo de datos](modelo_datos.md) y [Decisiones](decisiones.md).
4. El motor: [`dwh/transf_py/leadgen/README.md`](../../dwh/transf_py/leadgen/README.md).

## Accesos que pedir

- VPN y usuario de ClickHouse con los permisos de [`dwh/governance/`](../../dwh/governance/README.md).
- El `.env` (ClickHouse, Apify, OpenAI).
- SharePoint de TWP (Word y Excel de inputs) y los extractos de clientes.

## Puesta en marcha local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r dwh/transf_py/leadgen/requirements.txt -r dwh/transf_dbt/requirements.txt \
            -r dwh/transf_dbt/dev-requirements.txt
playwright install chromium     # navegador para las webs que solo se ven con JavaScript
python leadgen.py dbt -- deps   # paquetes de dbt (dbt_utils)
cp .env.example .env
./run_tests.sh
./run_db_tests.sh               # el motor y todos los modelos y tests de dbt en un ClickHouse local desechable
```

## Primer arranque contra la base de datos (una vez)

```bash
python leadgen.py init                                  # bases, tablas, vistas _current
python leadgen.py seed                                  # semillas del repositorio
python leadgen.py load-config "data/inputs/261020_New Leads DataApp Inputs v4.xlsx" --version v4-261020
mkdir -p data/geo && curl -L -o data/geo/at_bezirke.geojson "https://www.statistik.gv.at/gs-open/GEODATA/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=GEODATA:STATISTIK_AUSTRIA_POLBEZ_20260101&outputFormat=application/json&srsName=EPSG:4326"
python leadgen.py load-geo data/geo/at_bezirke.geojson --country AT --level SUBREGION \
    --source STATISTIK_AUSTRIA --version 2026 --code-prop g_id --name-prop g_name
# solo carga los distritos del Excel de TWP (por eso va después de load-config): en Austria salta
# los distritos de Viena 901-923, que se solapan con el 900 que usa TWP
# Alemania (BKG VG250) y Suiza (swisstopo) vienen como GeoPackage en coordenadas proyectadas:
# descargar, extraer el .gpkg del zip, convertir a GeoJSON con geo-convert y cargar igual
curl -L -o data/geo/vg250.zip "https://daten.gdz.bkg.bund.de/produkte/vg/vg250_ebenen_0101/aktuell/vg250_01-01.utm32s.gpkg.ebenen.zip"
python leadgen.py geo-convert DE_VG250.gpkg data/geo/de_kreise.geojson --crs 25832 \
    --sql "SELECT geom, AGS, GEN FROM vg250_krs WHERE GF = 4"          # GF = 4: tierra, sin mar ni lagos
python leadgen.py load-geo data/geo/de_kreise.geojson --country DE --level SUBREGION \
    --source BKG_VG250 --version 2026 --code-prop code --name-prop name
curl -L -o data/geo/sb3d.zip "https://data.geo.admin.ch/ch.swisstopo.swissboundaries3d/swissboundaries3d_2026-01/swissboundaries3d_2026-01_2056_5728.gpkg.zip"
python leadgen.py geo-convert swissBOUNDARIES3D_1_5_LV95_LN02.gpkg data/geo/ch_bezirke.geojson --crs 2056 \
    --sql "SELECT geom, bezirksnummer, name FROM tlm_bezirksgebiet UNION ALL SELECT geom, kantonsnummer * 100, name FROM tlm_kantonsgebiet WHERE kantonsnummer IN (4, 6, 7, 8, 9, 12, 16, 24, 25)"
# los cantones sin distritos (Uri, Obwalden, Nidwalden, Glarus, Zug, Basel-Stadt, Appenzell I.,
# Neuchâtel, Genève) son un distrito en el Excel de TWP, con código cantón × 100 (Genève 2500)
python leadgen.py load-geo data/geo/ch_bezirke.geojson --country CH --level SUBREGION \
    --source SWISSTOPO --version 2026 --code-prop code --name-prop name
# los listados de clientes que normaliza TWP (pestaña Customers), uno por país; la fecha es la del
# prefijo del nombre del fichero
python leadgen.py load-customers "data/inputs/260929_Customers_Fluidra Austria_leads_generator.xlsx" \
    --country AT --source-system TWP_SHEET_AT --extract-date 2026-09-29
python leadgen.py load-customers "data/inputs/261006_Customers_Astral Switzerland_Leads generator.xlsx" \
    --country CH --source-system TWP_SHEET_CH --extract-date 2026-10-06
python leadgen.py load-customers "data/inputs/261118_Customers_Fluidra Deutschland_leads_generator.xlsx" \
    --country DE --source-system TWP_SHEET_DE --extract-date 2026-11-18
python leadgen.py dbt                                   # las vistas
python leadgen.py precheck --country AT --areas AT-501 --terms Poolbau
```

Los códigos de subregión de los límites oficiales tienen que ser los del modelo de mercado de TWP (por
ejemplo 601 para Graz): `--code-prop` es la propiedad del GeoJSON que los lleva. La captación envía a Apify
el contorno de cada zona, sacado de estos límites: sin ellos, falla antes de gastar.
