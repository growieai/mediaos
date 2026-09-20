# Request permission for Cámara source discovery

The Cámara connector is disabled until an approved access method is confirmed. A website login or a Chamber membership does not by itself establish permission for automated access. BDNS and BOE discovery can continue independently.

The official electronic-office contact is **soporte.sede@camara.es**. Its contact information and automation restrictions were checked on 21 September 2026 on the [Cámara electronic-office ownership and conditions page](https://sede.camara.es/sede/html/titularidad).

Send the request below from your business email after replacing the bracketed fields. This file is a draft only; no message has been sent.

## Copyable request in Spanish

**Para:** soporte.sede@camara.es
**Asunto:** Consulta sobre API o autorización de consulta automatizada de convocatorias públicas

Buenos días:

Soy [nombre y cargo], de [razón social y sitio web]. Estamos desarrollando Growie Media OS, una herramienta que ayuda a identificar y explicar oportunidades públicas para autónomos y pequeñas empresas en España.

Queremos consultar los listados públicos de programas y convocatorias de la Cámara de España para identificar novedades y cambios en plazos. Hemos leído las condiciones de uso de la sede electrónica y no hemos habilitado la consulta automatizada de esta fuente.

¿Disponen de una API, un feed, un conjunto de datos abierto o algún otro canal autorizado para este fin? Si no existe, ¿podrían indicarnos si es posible obtener autorización escrita para consultar de forma automatizada, únicamente en modo lectura, los listados y las fichas públicas que ustedes determinen?

El alcance que proponemos es el siguiente:

- Recoger título, programa, Cámara convocante, territorio, fechas de solicitud, enlace oficial y texto público necesario para acreditar esos datos.
- Conservar la fecha de consulta, el enlace y una copia del contenido autorizado para comprobar cambios y mantener la trazabilidad de la información.
- Utilizar esos datos para preparar contenido informativo con atribución y enlace a la fuente oficial, sujeto a revisión humana. Parte del contenido se presentaría mediante una creadora virtual identificada expresamente como generada con IA.
- No acceder a zonas autenticadas, solicitudes, expedientes ni datos personales; tampoco presentar solicitudes ni realizar trámites.
- Identificar las peticiones mediante un User-Agent y un correo de contacto, utilizar caché y respetar los límites de frecuencia, horarios y demás condiciones que establezcan. Como punto de partida proponemos un único proceso, una revisión diaria y al menos diez segundos entre solicitudes, sujetos a su aprobación y a un máximo de peticiones acordado.

Les agradeceríamos que nos indicaran las URLs o recursos autorizados, los límites de uso, las condiciones de conservación y reutilización del texto público y la forma de atribución requerida. Si la autorización debe solicitarse a cada Cámara territorial o a otro departamento, agradeceríamos la indicación del contacto adecuado.

No habilitaremos esta consulta automatizada hasta disponer de una vía autorizada y de las condiciones aplicables.

Muchas gracias por su ayuda.

[Nombre y apellidos]

[Cargo y razón social]

[Correo de contacto]
[Sitio web]

## What to provide after they respond

Share their written response or approved API/feed documentation, including:

1. The permitted URLs, API endpoints or feed, and which organization or territorial Chambers the permission covers.
2. The allowed access method, request frequency, concurrency, identification requirements and contact for incidents.
3. Whether public document capture, retention, factual reuse and attributed excerpts are permitted, and any limits or expiry of that permission.
4. Any required authentication setup. Put credentials in the application's secret configuration when that integration is prepared; do not paste passwords or API keys into chat or commit them to Git.

A referral, account login or general acknowledgement is not sufficient to enable the connector. The response needs to identify an authorized method or explicitly permit the proposed automated use. We will implement only that confirmed scope and then run a small live verification before marking Cámara discovery available.
