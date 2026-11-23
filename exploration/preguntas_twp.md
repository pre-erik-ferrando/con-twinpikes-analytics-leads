# Preguntas para TWP y Fluidra

Registro de todo lo que les preguntamos, enviado o por enviar, con su estado. Para la primera entrega:
Pool specialist en Alemania, Austria y Suiza.

## Por enviar

1. **Categorías de Google sin clasificar.** En la primera búsqueda real (Carintia) se perdieron negocios de
   piscinas porque su categoría de Google no está en la pestaña Google categories: «Metallbauunternehmen»
   (un constructor de piscinas de acero inoxidable) y «Hersteller» (un fabricante de cubiertas de piscina).
   ¿Las añadís como CONDITIONAL? Así pasan si el nombre lleva una palabra de piscina.
   Hoy solo pasan estas categorías (Excel v4, pestaña Google categories); cualquier otra queda fuera:
   - INCLUDE (pasan siempre): `Schwimmbeckenbauer`, `Poolbedarf`, `Schwimmbecken-Reinigungsdienst`,
     `Schwimmbecken-Reparaturdienst`.
   - CONDITIONAL (pasan con una palabra de piscina en el nombre o la descripción): `Whirlpool-Fachhandel`,
     `Bauunternehmen`, `Baufachhandel`, `Gartenbauer`, `Saunageschäft`, `Schwimmbad`, `Erdbauunternehmen`,
     `Wasseraufbereitungsanlage`, `Baggerunternehmen`, `Bäderstudio`, `Generalunternehmer`, `Möbeltischler`,
     `Ofengeschäft`, `Sauna`, `Service establishment`, `Straßenbauunternehmen`,
     `Straßenbefestigungsunternehmen`, `Teichanlagenbauer`, `Teichbedarf`, `Transportdienst`.
   - EXCLUDE (fuera siempre): `Baumarkt`, `Bed and Breakfast`, `Gartencenter`, `Glaser`.
2. **Empresas sin categoría.** Una ficha de Google sin categoría queda fuera aunque se llame «…pool…». ¿Os
   parece que pase a revisión en lugar de excluirse? Cambia la regla 8 de la definición (DT.4.4.1).
3. **«Therme» en el nombre.** En Estiria (23-11) quedó fuera «… Gesellschaft für Wassertechnik Schwimmbad und
   Therme GmbH», categoría `Schwimmbeckenbauer`: la regla de nombre «Therme» (PUBLIC_POOL) es HARD y, según la
   definición, una regla HARD gana a una categoría INCLUDE. ¿La pasamos a SOFT? Entonces los constructores de
   piscinas con «Therme» en el nombre pasarían, y una terma pública sin categoría de piscina seguiría fuera.
4. **Tiendas de whirlpools y spas.** `Whirlpool-Fachhandel` es CONDITIONAL y pide una palabra de piscina, que
   cuenta solo al principio de una palabra: «Whirl & Spa(ss)» (Estiria) quedó fuera, y un nombre como
   «… Whirlpools» tampoco pasaría. ¿La pasamos a INCLUDE, o añadimos «whirlpool» y «spa» a las palabras de
   piscina (pestaña Pool words)?

## Enviadas el 19-11-2026

| # | Pregunta | Estado |
|---|---|---|
| 1 | Excel de inputs v4 | Respondida: recibida la v4 (261020). Siguen pendientes, en la pestaña de exportación, el formato de Province y Country, el valor de Lead Status y si el correo es obligatorio |
| 2 | Pestaña Subregion → sales rep, y a quién van los leads de un distrito sin comercial | Pendiente: vacía en la v4 («Pending Fluidra», pedida a Marc). Bloquea la exportación |
| 3 | Listados de clientes de Austria, Suiza y Alemania, con el IVA o registro de las sociedades de Fluidra | Respondida en parte: recibidos los tres listados (19-11). Faltan los números de las sociedades de Fluidra |
| — | Aviso: enviamos a Apify cada zona como su contorno | Dado |

## Más adelante

- Reglas de los otros cuatro perfiles.
- Listados de clientes de Bélgica y Países Bajos (TWP, 19-11: «está pedido»).
- Listados de clientes de Austria y Suiza más recientes: los recibidos tienen más de 7 días (AT del 29-09,
  CH del 06-10).
- ¿Quién aprueba una ejecución que supera el umbral de coste?
- Fichero de estados de Salesforce: cabeceras y valores exactos, y quién lo deja cada mes (definición v3.3,
  DT.8.4).
