# Preguntas para TWP y Fluidra

Lo que necesitamos para la primera entrega del Lead Generator: Pool specialist en Alemania, Austria y
Suiza. Lo que la definición v3.2 (14 de octubre de 2026) ya fija no se pregunta: se sigue el documento
más reciente.

## Lo que necesitamos ahora

1. ¿Podéis enviarnos el Excel de inputs v4 del 14 de octubre (261014_New_Leads_DataApp_Inputs v4)?
   Sin él, el motor solo funciona con valores de prueba: zonas, categorías, marcas, cuadrantes y
   comerciales salen de ese fichero. Avisadnos también cuando cambie. En la pestaña Salesforce and MC
   export, indicadnos además cómo escribir Province y Country en Salesforce, y si exige un correo de
   relleno cuando el lead no tiene.
2. En ese Excel (261014_New_Leads_DataApp_Inputs v4), la pestaña Subregion → sales rep debe decir qué
   comercial de Fluidra lleva cada distrito (por ejemplo, 601 Graz → su comercial). La definición la da
   como pendiente de Fluidra. ¿La tenéis ya? Sin ella, los leads se exportan a Salesforce sin comercial y
   la exportación se bloquea. Y si algún distrito no tiene comercial, ¿a quién asignamos sus leads?
   Proponemos un comercial por defecto.
3. ¿Podéis enviarnos los listados de clientes de Fluidra de Austria, Suiza y Alemania? Los usamos para
   no proponer como lead a quien ya es cliente. Según la definición: de Austria y Suiza, las pestañas que
   preparáis vosotros (y, cuando llegue, el extracto en bruto del ERP); de Alemania, "Extracción clientes
   FD". Hay que renovarlos cada mes. Con ellos, los números de IVA o de registro de las sociedades de
   Fluidra, para no proponer a la propia Fluidra como lead.

## Para confirmar antes de la primera exportación

4. Exportamos los leads nuevos con Lead Status "New". ¿Es el valor correcto en Salesforce?

## Aviso

Ahora enviamos a Apify cada zona como su contorno oficial en el mapa, en lugar de por su nombre: por
nombre, Apify no devuelve resultados. Sigue sin ser texto libre, aunque cambia lo que dice el apartado
DT.3.2. Si no os parece bien, decídnoslo.

## Más adelante (no bloquea la primera entrega)

Para TWP:

- Reglas, categorías, exclusiones, palabras de piscina y tipologías de los cuatro perfiles además de Pool
  specialist, con casos de prueba de cada uno. Hace falta al activar esos perfiles.
- Datos de Bélgica y Países Bajos: palabras de piscina en neerlandés, categorías de Google, marcas y
  cadenas. Hace falta para el piloto de esos países.
- ¿Cuándo se apaga el cockpit y qué lo sustituye? Hasta entonces, los resultados de llamada se cargan
  desde el CSV del cockpit.
- ¿Quién aprueba una ejecución cuando su coste supera el umbral? Hoy la aprueba quien la lanza.
- Viena: la definición dice que Austria tiene 94 Bezirke. Los límites oficiales traen 93, más Viena
  entera (900), más sus 23 distritos (901–923). Entendemos que usáis el 900. Probablemente lo responde
  el Excel v4. Solo importa al buscar en Viena.

Para Fluidra (a través de TWP):

- Listados de clientes de Bélgica y Países Bajos. Hace falta para el piloto de esos países.
- ¿Qué motivo de rechazo usa Salesforce? Hace falta cuando Salesforce sustituya al cockpit.
