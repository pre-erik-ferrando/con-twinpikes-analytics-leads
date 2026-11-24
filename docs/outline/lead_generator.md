# Lead Generator

**Resumen**: busca en Google Maps empresas de piscinas de Alemania, Austria y Suiza que todavía no son
clientes de Fluidra, las puntúa con el modelo de perfil de TWP y, tras la validación de Sales Ops, las
entrega a Salesforce para que Inside Sales las llame por orden de interés. Las pantallas las construye
Intelek sobre las vistas `lgn_v_*`. El proyecto (alcance, decisiones, pendientes):
[Proyecto Lead Generator](proyecto_lead_generator.md).

## Doc. Funcional

### Qué hace (DF.1)

Busca en Google Maps por perfil, término y zona; aparta lo que no es negocio de piscina y lo que ya es
cliente; lee la web de cada empresa, la puntúa con el modelo de perfil de TWP y deja que Sales Ops
rechace lo que no vale. Los aprobados se exportan a Salesforce antes de la llamada. Todo lo encontrado
se guarda: si cambian las reglas, se vuelve a filtrar y puntuar sin pagar otra vez la búsqueda ni la
lectura de las webs.

### El recorrido de un lead

| Paso | Comando | Resultado |
|---|---|---|
| Captación | `leadgen.py capture` | Una tanda de captación (`capture_batch`): encontrados, excluidos por el filtro y por ser cliente, estimación de coste del enriquecimiento |
| Enriquecimiento | `leadgen.py enrich` | Webs leídas, análisis del modelo comprobado, segundo cruce con clientes, puntuación y Rating |
| Validación | Intelek (o `review`, `reject`, `approve-rest`) | Rechazos con motivo; el resto aprobado con alcance explícito |
| Exportación | `leadgen.py export` + `confirm-export` | Fichero de Salesforce; Published al confirmar la subida |
| Estado en Salesforce (mensual) | `leadgen.py load-sf-status` | Los descartados no vuelven a salir; mapa 3 por zona y mes |
| Actualización mensual | `load-customers` + `rematch` | Los leads que ya son clientes se descualifican |

Estados del lead (DF.4), calculados en `lgn_v_lead.des_status`: Found, Excluded by engine, To review,
Rejected, Approved, Published.

## Doc. Técnica

| Página | Qué cuenta |
|---|---|
| [Arquitectura](arquitectura.md) | El flujo, las capas y el reparto entre el motor en Python y dbt |
| [Modelo de datos](modelo_datos.md) | Las tablas de cada base `fld_*lgn` y la identidad de una empresa |
| [Configuración](configuracion.md) | Las tablas `biz_cfg_*`, de dónde salen sus valores y la comprobación previa |
| [Operación](operacion.md) | Qué se lanza cada semana y cada mes, y cómo seguir una ejecución |
| [Onboarding](onboarding.md) | Qué leer, qué accesos pedir y cómo ponerlo en marcha |

El entorno común del cliente (DWH, GitHub, orquestación, consumo) está en *Arquitectura técnica del
entorno*, compartido con el Insight Generator.
