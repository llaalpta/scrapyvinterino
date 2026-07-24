# Investigacion Catalogo Vinted

Fecha de observacion inicial: 2026-07-02.

Ultima actualizacion: 2026-07-24.

## Evidencia del documento de detalle

- Un HAR de navegacion directa a `/api/v2/items/{id}/details` muestra respuestas `403` con `cf-mitigated: challenge`; el `code:104 not_found` final pertenece a un POST generado por Cloudflare y no es un resultado fiable de consulta del articulo.
- Un segundo HAR de catalogo a articulo muestra un documento publico `/items/{id}-{slug}?referrer=catalog` correcto y ninguna XHR de detalle. JSON-LD y los registros Next/React Flight del documento contienen los datos necesarios.
- Los ids de registro Flight cambian por render. La extraccion debe parsear registros `id:JSON`, comprobar el item solicitado y localizar bloques de item, plugins y precios mediante firmas estructurales.
- `plugins` aporta descripcion, atributos, estado y senales de transaccion; el bloque del item aporta fotos firmadas y comprabilidad; `shippingDetails.price` es el envio minimo mostrado; `pricingServices` aporta base, proteccion y total sin envio.
- Las URL firmadas `images*.vinted.net/.../f800/...?...` cargan sin cookies de Vinted. El backend guarda solo las URL y el navegador las descarga directamente conservando la firma y la query.
- Los HAR son entradas locales de investigacion y no se incorporan al repositorio porque contienen material bruto de navegacion y sesion.
- El documento decodificado observado mide unos 2.35 MB. Titulo, canonical y meta descripcion aparecen antes de 16 KB, pero shipping/pricing termina alrededor del 96.5 %, plugins al 97.2 % y el item rico al 97.8 %.
- Cortar cuando el detalle aceptado parece completo ahorraria solo unos 6 KB comprimidos y cerca de 2 ms en el HAR; no compensa perder overrides o bloqueos tardios. Un descarte inequívoco por titulo/descripcion si puede evitar casi todo el cuerpo, pero debe validar equivalencia y continuidad de sesion antes de activarse.
- Los cinco detalles residenciales auditados rotaron la cookie de sesion. La concurrencia debe aislar jars, conservar orden logico y validar el contexto resultante; compartir el jar mutable entre hilos no es un contrato seguro.
- El parser selectivo `next_flight_v3` sobre el HAR real decodifico 20 de 224 registros: mediana `59.7 ms`, p95 `68.8 ms`, cinco fotos y los mismos valores de envio, total y disponibilidad.
- Dos canarios C2 residenciales completaron cinco detalles y validaron la rama final aunque `_vinted_fr_session` divergiera. La version con lanes persistentes tardo `5.034 s`; el control C1 reutilizando una sola conexion tardo `4.598 s`, por lo que la concurrencia no se promueve con este proxy.
- Diez descartes de head forzados recibieron `15-22 KB` y tardaron `455-636 ms`; cada lote termino con API de catalogo aceptada. Esto valida transporte/sesion, no la equivalencia de cualquier descripcion, que permanece en observacion.
- El HAR de detalle contiene claves de traduccion `item.view_count.*`, pero no un valor de visitas del articulo. Algunos feeds pueden exponer `view_count`; el contrato lo acepta solo como entero no negativo opcional del JSON de catalogo y nunca abre una peticion adicional para obtenerlo.
- La meta descripcion observada concatena `titulo - descripcion`. El descarte temprano compatible con filtros solo-descripcion debe separar un prefijo de titulo exacto; buscar sobre la meta completa produciria falsos descartes por palabras presentes solo en el titulo.
- El gate `description_only_v2` comparo el HAR mas 29 documentos vivos: 23 descripciones aislables, 20 descartes potenciales y cero falsos positivos respecto a Flight. Diez cierres forzados y espaciados recibieron `16-22 KB`, no produjeron `429`/challenge y terminaron con catalogo aceptado; `enforced` pasa a ser el valor estable.
- Los 29 candidatos vivos del catalogo ES incluyeron `view_count`; todos valian `0`, por lo que cero se conserva como dato y no se confunde con ausencia.
- La auditoria independiente de `884c533` reproceso offline el HAR de navegacion disponible: el documento de `2,350,064` bytes produjo titulo, canonical y descripcion Flight, y el sufijo meta aislado de 24 caracteres fue semanticamente identico a la descripcion Flight normalizada. Los casos de titulo-only y shadow equivalence quedan cubiertos a nivel provider con una sola GET simulada.
- El escaneo de 505 eventos locales encontro un unico `response.body_snippet` HTML historico en un probe fallido y ningun secreto, userinfo o URL firmada. El provider pasa a registrar solo observaciones de longitud/tipo, la redaccion bloquea claves de contenido y `0015_redact_event_bodies` elimina snippets existentes de forma irreversible; el reescaneo termino con cero hallazgos.
- El control final uso PostgreSQL desechable para `upgrade/downgrade/upgrade`, namespace Redis real aislado, 372 tests backend, Ruff, lint/build PWA y Playwright desktop/mobile sin errores ni overflow. No genero trafico nuevo contra Vinted.

## URL investigada

```text
https://www.vinted.es/catalog?search_text=&catalog[]=76&brand_ids[]=88&brand_ids[]=364&price_to=5.00&order=newest_first
```

La investigacion se hizo sin login, sin cuenta personal y sin tokens personales.

## Hechos observados

- La pagina publica carga articulos del catalogo en el HTML inicial de Next, dentro de scripts `self.__next_f.push(...)`.
- No se observo una XHR limpia de catalogo para los items principales durante la carga inicial.
- `GET` HTTP directo al HTML de catalogo funciono con un `User-Agent` de navegador y devolvio `200 text/html`.
- El HTML directo contenia el stream de Next y campos de item como `id`, `title`, `price`, `brand_title`, `path`, `photo`, `size_title`, `status`, `favourite_count` y `user.login`.
- El endpoint candidato `GET /api/v2/catalog/items` respondio `401 invalid_authentication_token` sin autenticacion anonima adicional.
- La pagina tambien llama endpoints auxiliares como banners y promoted closets, pero esos no son la fuente principal de items del catalogo.

## Observacion adicional: catalogo JSON con sesion anonima publica

- `GET /api/v2/catalog/items` responde `401 invalid_authentication_token` cuando se llama sin cookies/tokens publicos.
- Cargar una pagina publica de Vinted emite cookies anonimas publicas, incluyendo `access_token_web` con scope publico.
- Con esa sesion anonima publica, `GET /api/v2/catalog/items` devolvio `200 application/json`.
- La sesion observada no usa cuenta personal, login ni token personal.
- El documento publico de catalogo se usa como bootstrap de sesion anonima, pero no debe ser el camino normal de extraccion del catalogo rapido ni un refresh provocado por una respuesta fallida.
- En HAR de catalogo con Chrome 146 se observo CSRF en el documento/bundle y `x-anon-id` en peticiones posteriores; el proveedor debe extraerlos cuando existan y reenviarlos al API con la misma sesion HTTP.
- En el HAR valido `www.vinted1462.es.har`, el documento de catalogo usa `User-Agent` Chrome 146 y `sec-ch-ua` Chrome 146 con `Accept-Language: en-GB,en;q=0.9`, mientras la respuesta mantiene `x-user-iso-locale: es-ES` y `x-screen: catalog`. Por tanto, `Accept-Language` no se valida como prefijo obligatorio del locale; se trata como parte del perfil observado.
- Si el API JSON falla por autenticacion o sesion, la primera respuesta invalida el contexto y termina esa ejecucion sin refresh ni reintento.
- Si el API JSON devuelve `429`, no se asume DataDome solo por el status: `Retry-After` se registra como diagnostico, pero no autoriza espera, refresh ni reintento.
- El fallo terminal afecta a esa ejecucion y queda visible; no detiene API, PWA, worker ni otras fuentes.

Benchmarks locales observados para la misma busqueda:

| Camino | Tamano aproximado | Latencia aproximada |
| --- | ---: | ---: |
| HTML catalogo completo | 8.6 MB | 2.2 s |
| API catalogo `per_page=96` | 1.39 MB | 0.9 s |
| API catalogo `per_page=24` | 328 KB | 0.38 s |
| API catalogo `per_page=5` | 72 KB | 0.24 s |
| API catalogo `per_page=3` | 41 KB | 0.24 s |

Decision de rendimiento:

- Para monitorizacion rapida, usar API JSON con `order=newest_first`, `page=1` y `per_page=5` por defecto.
- No mantener fallback al HTML de catalogo en el camino rapido.
- Mantener el documento HTML de catalogo solo como bootstrap/refresh de sesion anonima publica, no como fallback de extraccion de items.
- Mantener parseo HTML como conocimiento de investigacion, no como camino operativo preferente.

## Parametros y paginacion

- La URL publica usa arrays como `catalog[]=76` y `brand_ids[]=88&brand_ids[]=364`.
- Algunos endpoints auxiliares convierten esos filtros a `catalog_ids=76` y `brand_ids=88,364`.
- Para el API JSON, la URL publica se debe traducir a parametros de API conservando la semantica de filtros.
- `page=2` en la URL publica devolvio otra pagina con `pagination.current_page=2`.
- El payload observado contenia:
  - `current_page`;
  - `total_pages`;
  - `total_entries`;
  - `per_page`;
  - `time`.
- Con la URL de prueba se observaron 96 items por pagina.

## Contrato HTTP minimo observado para HTML publico

- Metodo: `GET`.
- URL: la URL publica de catalogo guardada en `search_sources.url`.
- Parametro de pagina: `page=N` anadido a la URL publica cuando `N > 1`.
- Headers minimos usados para reproducir la prueba:
  - `User-Agent`: navegador desktop moderno.
  - `Accept`: `text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8`.
  - `Accept-Language`: `en-GB,en;q=0.9` en el HAR Chrome 146 valido usado como referencia runtime actual; observaciones anteriores usaban `es-ES,es;q=0.9,en;q=0.8`.
- Cookies requeridas en esta observacion: ninguna.
- Redirecciones: seguir redirecciones.
- Respuesta esperada: `200 text/html` con scripts `self.__next_f.push(...)`.
- Condicion de fallo: respuesta sin stream de Next o sin `items.items[]` debe registrarse como error de proveedor, no como lista vacia exitosa.

## Contrato HTTP rapido decidido para catalogo

- Bootstrap anonimo:
  - `GET` a la URL publica de catalogo guardada en el monitor con headers de navegador.
  - Mantener la copia HTTP activa solo en memoria del provider; la copia reutilizable se serializa cifrada en PostgreSQL y nunca pasa a Redis, logs o respuestas API.
  - Extraer CSRF, anon id, `v_udt`, locale y screen cuando aparezcan en el documento, headers o cookies.
- Catalogo rapido:
  - `GET /api/v2/catalog/items`.
  - Parametros observados:
    - `catalog_ids=76`;
    - `brand_ids=88,364`;
    - `price_to=5.00`;
    - `order=newest_first`;
    - `page=1`;
    - `per_page=5` por defecto para monitorizacion.
  - Headers:
    - `User-Agent`;
    - `Accept` coherente con el perfil de navegador runtime;
    - `Accept-Language`;
    - `Referer` con la URL publica de busqueda.
    - `X-CSRF-Token` y `X-Anon-Id` cuando el bootstrap los haya obtenido.
  - Si devuelve `401`, `403` o HTML inesperado, invalidar la sesion y marcar la ejecucion fallida en la primera respuesta, sin refresh ni retry.
  - Si devuelve `429`, parsear `Retry-After` en segundos o HTTP-date solo para diagnostico y no clasificarlo como DataDome sin senales adicionales; terminar sin espera ni segunda llamada.
  - Un JSON sin `items` sigue siendo un error generico de contrato, no una autorizacion para HTML fallback.

## Mapeo a `items`

| Modelo interno | Campo observado |
| --- | --- |
| `vinted_item_id` | `id` |
| `title` | `title` |
| `brand` | `brand_title` |
| `price_amount` | `price.amount` |
| `currency` | `price.currency_code` |
| `size` | `size_title` |
| `status` | `status` |
| `seller_login` | `user.login` |
| `seller_country` | No observado en el listado de catalogo |
| `favorite_count` | `favourite_count` |
| `url` | `path` o `url`, resuelto contra `https://www.vinted.es` |
| `image_url` | `photo.url` |
| `raw` | Subconjunto sanitizado del item |

## Contrato decidido

- `VintedCatalogProvider.search(source, page=None) -> CatalogSearchResult`.
- `CatalogSearchResult` contiene items normalizados, paginacion y metadatos del proveedor.
- `CatalogItemCandidate` representa un item publico antes de persistirlo.
- La implementacion rapida debe usar HTTP directo al API JSON con sesion anonima publica.
- El documento HTML publico debe reservarse para bootstrap/refresh de sesion anonima y para investigacion.
- Playwright queda reservado para investigacion o para obtener contexto anonimo si en el futuro cambia el comportamiento.

## Sanitizacion

- No guardar en claro ni fuera del contexto cifrado cookies, tokens o cabeceras de sesion; no guardar IDs de usuario reales, direcciones ni datos de pago.
- Las cookies/tokens publicos anonimos pueden existir en memoria de proceso y cifrados en `vinted_sessions.context_encrypted`; nunca deben persistirse en claro, fixtures, logs ni respuestas API.
- Los fixtures deben usar valores sinteticos o sanitizados.
- No guardar parametros de tracking completos, `search_tracking_params`, URLs de perfil ni payloads de usuario completos.
- Guardar solo el subconjunto necesario para probar mapeo.

## Fallos y riesgos

- El endpoint JSON de catalogo requiere sesion anonima publica; sin ella responde `401`.
- El stream de Next es una estructura interna y puede cambiar.
- DataDome/captcha puede aparecer segun IP, frecuencia o entorno.
- La disponibilidad de campos opcionales no esta garantizada; el parser debe tolerar campos ausentes.
- Si el API JSON deja de responder con items tras refrescar sesion anonima, la implementacion debe registrar error e intentar bypass agresivo si fuese necesario.

## Verificacion realizada

- Navegador anonimo cargo la URL publica y mostro resultados.
- HTTP directo al HTML devolvio `200` y contuvo el payload de catalogo.
- `page=1` y `page=2` devolvieron paginas distintas con paginacion coherente.
- Endpoint candidato `/api/v2/catalog/items` devolvio `401` sin token.
- Tras bootstrap anonimo por HTML publico, `/api/v2/catalog/items` devolvio `200` con JSON de catalogo.
- `per_page=3`, `5`, `10`, `24` y `96` funcionaron en la observacion.
- Se creo fixture sanitizado para probar mapeo sin datos sensibles reales.

## DataDome y anti-bot

Observacion: 2026-07-05.

Vinted usa DataDome como WAF. DataDome analiza multiples capas de la conexion:

- **TLS/JA3 fingerprint**: el ClientHello TLS revela cipher suites, extensiones y curvas elipticas en orden especifico. `httpx` y `requests` producen huellas de Python que son trivialmente detectables.
- **HTTP/2 SETTINGS frame**: el frame SETTINGS de HTTP/2 (HEADER_TABLE_SIZE, MAX_CONCURRENT_STREAMS, INITIAL_WINDOW_SIZE) y el orden de pseudo-headers (`:method`, `:authority`, `:scheme`, `:path`) difieren entre clientes.
- **Orden de headers HTTP**: Chrome envia headers en un orden especifico. DataDome valida que el orden coincida con la huella TLS declarada.
- **Sec-Ch-Ua coherencia**: si el User-Agent dice Chrome/136 pero `sec-ch-ua` dice Chrome/133, DataDome detecta la inconsistencia.
- **Timing**: un bot hace bootstrap + catalogo en <50ms. Un humano tarda 1-4 segundos. DataDome correlaciona latencia con trust score.
- **Cookie datadome**: DataDome emite una cookie `datadome` cuyo valor codifica un trust score. Si se pierde o manipula, DataDome puede servir un challenge.
- **IP reputation**: DataDome mantiene bases de datos de reputacion por IP. IPs de datacenter son mas sospechosas que residenciales.

### Contrato sticky de DataImpulse

Observacion documental: 2026-07-24. Fuentes oficiales: [Session ID](https://docs.dataimpulse.com/proxies/parameters/session-id), [Session Interval](https://docs.dataimpulse.com/proxies/parameters/session-interval), [Protocols](https://docs.dataimpulse.com/proxies/protocols), [Types of connections](https://docs.dataimpulse.com/proxies/types-of-connections) y [User API](https://documenter.getpostman.com/view/7041120/2sAY4rGRZC).

- El puerto HTTP/HTTPS `823` es rotatorio por peticion cuando el username no incorpora afinidad. El parametro `sessid.<valor>` en ese mismo username selecciona una IP durante aproximadamente 30 minutos; el ID puede ser cualquier string o numero y no requiere una llamada API para crear la afinidad.
- La documentacion de `sessttl` usa conexiones sticky por puerto, con ejemplos en `10000`. No documenta que `sessttl` y `sessid` puedan combinarse sobre `823`, por lo que runtime no debe asumir esa compatibilidad.
- `GET /api/rotate_ip` resetea una asignacion sticky por puerto o `sessid` y exige al menos 30 segundos entre resets de la misma sesion. No promete que el siguiente peer fisico sea distinto. Un ID nuevo mas una observacion neutral de egress es un contrato mas simple y verificable para el producto actual.
- `GET /api/list` devuelve conexiones formateadas y admite cantidad, tipo y TTL. Varias lineas del gateway/cuenta no equivalen a varios proveedores independientes; el fallback de la app requiere perfiles configurados de forma explicita.
- `14.54.1` fija 25 minutos como margen local inicial para los perfiles DataImpulse y conserva el monitor mas alla de ese TTL. Las tareas posteriores de 14.54 evitan integrar `rotate_ip` y deben rechazar una rotacion observada hacia la misma IP antes de volver a Vinted.

### Bypass implementado

El ciclo de vida mantenido y su contrato vigente estan en `docs/architecture.md` y `docs/specs/010-producer-consumer-bypass.md`; esta seccion conserva solo conclusiones tecnicas actuales y divergencias explicitamente asignadas.

- `curl_cffi` con `impersonate` replica exactamente el ClientHello TLS, HTTP/2 SETTINGS, y orden de pseudo-headers de la version de Chrome especificada.
- Pool de perfiles de navegador coherentes: cada sesion usa un perfil con `impersonate`, `User-Agent`, y `Sec-Ch-Ua*` alineados.
- Delay humano con distribucion Beta entre bootstrap y catalogo.
- Deteccion de challenge: si la respuesta contiene cabeceras `x-datadome*`, cookie `datadome` no vacia en una respuesta de error, `server` DataDome o marcadores HTML (`geo.captcha-delivery.com`, `dd.js`, `t.datadome.co`), se descarta la sesion/proxy segun la politica de run. Una cookie `datadome` en `200` de bootstrap puede ser contexto valido y no basta por si sola para declarar challenge.
- Un `429` sin firmas DataDome se considera rate limit de catalogo, no challenge; se registra `Retry-After` de forma saneada y se termina la ejecucion sin backoff ni reintento.
- Proxies residenciales con UUID sticky nuevo por preparacion y reutilizado por runs elegibles del mismo monitor/perfil. El formato del username y su TTL maximo se persisten en cada perfil; nuevos y migrados usan `{username};sessid.{session_id}` y `25` minutos. El binding efectivo combina un contador monotono con un HMAC versionado de transporte, credenciales, preset, template y TTL. El run lo revalida bajo advisory ownership compartido antes de construir proveedor; una edicion usa ownership exclusivo, avanza el contador e invalida el contexto anterior sin exponer el preimage.
- No existe una escalada generica a otro perfil. El primer challenge es terminal, el consumer hace ACK sin otra llamada al provider y cualquier retry, nueva IP/perfil o delay como fallback requiere una decision de producto separada.

### Scripts de verificacion

- `scripts/check_ja3.py`: verifica JA3 contra servicios de echo publicos.
- `scripts/check_headers.py`: compara headers con referencia de Chrome real.
- `scripts/check_datadome.py`: smoke test de bootstrap + catalogo con deteccion de challenge.
- `scripts/inspect_vinted_session.py`: captura headers/cookies/navigator de Chrome real via Playwright + CDP.
- `scripts/compare_fingerprints.py`: diff entre Chrome real y curl_cffi.
