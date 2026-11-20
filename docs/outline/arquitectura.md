# Arquitectura

## Flujo

```
 Google Maps (Apify) ─┐
 Webs (crawler)       │                    motor Python                    dbt (vistas)
 Modelo (OpenAI)      ├─▶ fld_landinglgn ─▶ dwh/transf_py/leadgen ─▶ *_out ─▶ src → stg → crp → biz ─▶ Intelek
 Extractos clientes   │   (tal cual)        normaliza, identifica,          lgn_e  lgn_a  lgn_d  lgn_f / lgn_v
 Estados Salesforce   │                     filtra, cruza, puntúa              ▲
 Límites oficiales  ──┘                          ▲                            │
                                                 └── biz_cfg_* y biz_lead_validation (los escribe Intelek)
                                                      fld_opslgn: ejecuciones, instantáneas, coste
```

- **El motor** (`dwh/transf_py/leadgen`, lanzado con `leadgen.py`) hace todo lo que decide: captación,
  normalización, identidad, filtrado, cruce con clientes, rastreo, modelo, comprobación, puntuación y
  exportación. Escribe en `fld_landinglgn` lo recibido tal cual, en `fld_opslgn` el control de cada
  ejecución y en `fld_bizlgn` sus resultados (`<paso>_out`).
- **dbt** son solo vistas: tipan landing (src), dan la configuración vigente (stg), conforman empresas,
  fichas, clientes y enriquecimiento (crp) y publican lo que lee Intelek (biz, `lgn_v_*`).
- **Intelek** lee las vistas y escribe la configuración (`biz_cfg_*`) y las decisiones de validación
  (`biz_lead_validation`), aprobaciones y peticiones de supresión. El motor las lee al arrancar cada
  ejecución.

## Capas (DT.1.3)

| Capa | Base de datos | Qué contiene | Quién la escribe |
|---|---|---|---|
| landing | `fld_landinglgn` | Lo que llega de fuera, tal cual | Motor |
| src | `fld_srclgn` | Vistas tipadas sobre landing | dbt |
| stg | `fld_stglgn` | Configuración vigente | dbt |
| crp | `fld_crplgn` | Empresa, ficha, alias, cliente, extracto, enriquecimiento, resultado de llamada | dbt |
| biz | `fld_bizlgn` | Configuración y escritura de Intelek, resultados del motor, vistas de consumo | Intelek, motor, dbt |
| ops | `fld_opslgn` | Ejecuciones, instantáneas, consultas, envíos al modelo, coste, exportaciones | Motor |

## Principios

- **Solo inserción** (DT.1.2): ninguna tabla se actualiza; la fila vigente es la última por
  `(inserted_at, updated_by)`. Borrar una fila de configuración es insertarla con `active = 0`.
- **Instantánea**: cada ejecución congela la configuración al arrancar (`ops.config_snapshot`) y la
  conserva aunque se reanude días después.
- **No pagar dos veces**: el crudo de Apify y de las webs se guarda; cada petición al modelo se
  registra antes de enviarse; las respuestas se reutilizan si coinciden empresa, versión del prompt,
  marcas, candidatos y contenido.
- **Bloqueo por áreas**: una captación solo se rechaza si comparte áreas (la misma, una que la contiene
  o una contenida) con otra en marcha.
- **Trazabilidad**: cada dato de la web lleva la página de la que sale, comprobada sin modelo.

Tablas por capa: [Modelo de datos](modelo_datos.md). Configuración: [Configuración](configuracion.md).
