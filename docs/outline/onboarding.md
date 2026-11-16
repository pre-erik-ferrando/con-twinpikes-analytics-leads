# Onboarding

## Qué leer, en orden

1. [General](general.md) y [Proyecto Lead Generator](proyecto_lead_generator.md).
2. La definición v3.2, parte I (funcional), en [`docs/spec/`](../spec); la parte II y la revisión técnica
   cuando se vaya a tocar código.
3. [Arquitectura](arquitectura.md), [Modelo de datos](modelo_datos.md) y [Decisiones](decisiones.md).
4. El motor: [`dwh/transf_py/leadgen/README.md`](../../dwh/transf_py/leadgen/README.md).

## Accesos que pedir

- VPN y usuario de ClickHouse con los permisos de [`dwh/governance/`](../../dwh/governance/README.md).
- El `.env` (ClickHouse, Apify, Anthropic).
- SharePoint de TWP (Word y Excel de inputs) y los extractos de clientes.

## Puesta en marcha local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r dwh/transf_py/leadgen/requirements.txt -r dwh/transf_dbt/requirements.txt \
            -r dwh/transf_dbt/dev-requirements.txt
cp .env.example .env
./run_tests.sh
```

## Primer arranque contra la base de datos (una vez)

```bash
python leadgen.py init                                  # bases, tablas, vistas _current
python leadgen.py seed                                  # semillas del repositorio
python leadgen.py load-config data/inputs/261014_New_Leads_DataApp_Inputs_v4.xlsx --version v4-261014
python leadgen.py load-geo data/geo/at_bezirke.geojson --country AT --level SUBREGION \
    --source STATISTIK_AUSTRIA --version 2025 --code-prop id --name-prop name
python leadgen.py load-customers data/inputs/customers_at.xlsx --country AT --source-system TWP_SHEET_AT \
    --extract-date 2026-11-01
python leadgen.py dbt                                   # las vistas
python leadgen.py precheck --country AT --areas AT-501 --terms Poolbau
```

Los códigos de subregión de los límites oficiales tienen que ser los del modelo de mercado de TWP (por
ejemplo 601 para Graz): `--code-prop` es la propiedad del GeoJSON que los lleva.
