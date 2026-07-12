# Investigacion Catalogo Vinted

Fecha de observacion inicial: 2026-07-02.

Ultima actualizacion: 2026-07-12.

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
- El documento publico de catalogo se usa como bootstrap/renovacion de sesion anonima, pero no debe ser el camino normal de extraccion del catalogo rapido.
- En HAR de catalogo con Chrome 146 se observo CSRF en el documento/bundle y `x-anon-id` en peticiones posteriores; el proveedor debe extraerlos cuando existan y reenviarlos al API con la misma sesion HTTP.
- En el HAR valido `www.vinted1462.es.har`, el documento de catalogo usa `User-Agent` Chrome 146 y `sec-ch-ua` Chrome 146 con `Accept-Language: en-GB,en;q=0.9`, mientras la respuesta mantiene `x-user-iso-locale: es-ES` y `x-screen: catalog`. Por tanto, `Accept-Language` no se valida como prefijo obligatorio del locale; se trata como parte del perfil observado.
- Si el API JSON falla por autenticacion o sesion, el proveedor debe refrescar la sesion anonima en la misma sesion HTTP/cookie jar y reintentar una vez.
- Si el API JSON devuelve `429`, no se asume DataDome solo por el status: se respeta `Retry-After` cuando existe y esta dentro del presupuesto operativo antes de refrescar/reintentar.
- Si el reintento falla, debe fallar solo la ejecucion/fuente correspondiente y registrar error; no debe detener API, PWA, worker ni otras fuentes.

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
  - Guardar solo cookies/tokens publicos y contexto anonimo en memoria de proceso inicialmente.
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
  - Si devuelve `401`, `403`, HTML inesperado o JSON sin `items`, refrescar sesion anonima en la misma sesion HTTP y reintentar una vez cuando no haya firma DataDome.
  - Si devuelve `429`, parsear `Retry-After` en segundos o HTTP-date; esperar solo si el valor es valido y no supera el presupuesto operativo, y no clasificarlo como DataDome sin senales adicionales.
  - Si el reintento falla, registrar error y marcar la ejecucion como fallida.

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

- No guardar cookies, tokens, cabeceras de sesion, IDs de usuario reales, direcciones ni datos de pago.
- Las cookies/tokens publicos anonimos pueden existir en memoria de proceso para ejecutar peticiones, pero no deben persistirse en fixtures, logs ni respuestas API.
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

### Bypass implementado

- `curl_cffi` con `impersonate` replica exactamente el ClientHello TLS, HTTP/2 SETTINGS, y orden de pseudo-headers de la version de Chrome especificada.
- Pool de perfiles de navegador coherentes: cada sesion usa un perfil con `impersonate`, `User-Agent`, y `Sec-Ch-Ua*` alineados.
- Delay humano con distribucion Beta entre bootstrap y catalogo.
- Deteccion de challenge: si la respuesta contiene cabeceras `x-datadome*`, cookie `datadome` no vacia en una respuesta de error, `server` DataDome o marcadores HTML (`geo.captcha-delivery.com`, `dd.js`, `t.datadome.co`), se descarta la sesion/proxy segun la politica de run. Una cookie `datadome` en `200` de bootstrap puede ser contexto valido y no basta por si sola para declarar challenge.
- Un `429` sin firmas DataDome se considera rate limit de catalogo, no challenge; se registra `Retry-After`, backoff aplicado y presupuesto maximo antes de reintentar.
- Proxies residenciales con UUID de sesion sticky por intento. El formato del username depende del proveedor y se configura con `PROXY_STICKY_USERNAME_TEMPLATE`; por defecto usa `{username}-session-{session_id}`.
- Retry con escalada: nueva IP, nuevo perfil, delay creciente.

### Scripts de verificacion

- `scripts/check_ja3.py`: verifica JA3 contra servicios de echo publicos.
- `scripts/check_headers.py`: compara headers con referencia de Chrome real.
- `scripts/check_datadome.py`: smoke test de bootstrap + catalogo con deteccion de challenge.
- `scripts/inspect_vinted_session.py`: captura headers/cookies/navigator de Chrome real via Playwright + CDP.
- `scripts/compare_fingerprints.py`: diff entre Chrome real y curl_cffi.
