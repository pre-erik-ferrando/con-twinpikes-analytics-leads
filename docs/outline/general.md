# General

## El cliente

**TwinPikes** es el nombre del proyecto con **Fluidra**, que vende equipamiento para piscina. Este
repositorio es el **Lead Generator DACH**, el proyecto conjunto de Prenomics y **TWP** para Fluidra
Central Europe: encontrar empresas de piscinas en Alemania, Austria y Suiza (y, más adelante, Bélgica y
Países Bajos) que todavía no son clientes de Fluidra y entregarlas a Salesforce, puntuadas, para que
Inside Sales las llame por orden de interés.

## Modelo de relación

| Quién | Qué hace |
|---|---|
| **Fluidra** | Envía los extractos de clientes de cada país, recibe los leads en Salesforce y valida la definición |
| **TWP** | Define el modelo de perfil y de mercado: zonas y cuadrantes, perfiles y términos, exclusiones, puntuación y marcas (Excel de inputs) |
| **Prenomics** | Construye y opera el motor y la base de datos, y la configuración avanzada |
| **Intelek** | La DataApp: pantallas de captación, enriquecimiento, validación, exportación y configuración |

## Dónde está cada cosa

- **Código**: este repositorio. Mismo stack y convenciones que el Insight Generator
  (`con-twinpikes-analytics`), cuya plantilla copia.
- **Base de datos**: ClickHouse, bases `fld_*lgn` ([Arquitectura](arquitectura.md)).
- **Definición**: el Word v3.2 y su revisión técnica, en [`docs/spec/`](../spec); el Excel de inputs,
  en el SharePoint de TWP (fuera del repositorio).
- **Qué se ha construido y qué no**: [Proyecto Lead Generator](proyecto_lead_generator.md) y
  [Decisiones](decisiones.md).

Subpáginas: [Onboarding](onboarding.md).
