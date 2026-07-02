# Investigacion Catalogo Vinted

Fecha de observacion inicial: 2026-07-02.

Ultima actualizacion: 2026-07-02.

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
- El HTML publico puede usarse para bootstrap o renovacion de sesion anonima, pero no debe ser el camino normal de extraccion del catalogo rapido.
- Si el API JSON falla por autenticacion o sesion, el proveedor debe refrescar la sesion anonima y reintentar una vez.
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
- Mantener el HTML solo como bootstrap/refresh de sesion anonima publica.
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
  - `Accept-Language`: `es-ES,es;q=0.9,en;q=0.8`.
- Cookies requeridas en esta observacion: ninguna.
- Redirecciones: seguir redirecciones.
- Respuesta esperada: `200 text/html` con scripts `self.__next_f.push(...)`.
- Condicion de fallo: respuesta sin stream de Next o sin `items.items[]` debe registrarse como error de proveedor, no como lista vacia exitosa.

## Contrato HTTP rapido decidido para catalogo

- Bootstrap anonimo:
  - `GET` a una pagina publica de Vinted con headers de navegador.
  - Guardar solo cookies/tokens publicos en memoria de proceso inicialmente.
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
    - `Accept: application/json, text/plain, */*`;
    - `Accept-Language`;
    - `Referer` con la URL publica de busqueda.
  - Si devuelve `401`, `403`, captcha, HTML inesperado o JSON sin `items`, refrescar sesion anonima y reintentar una vez.
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
- El HTML publico debe reservarse para bootstrap/refresh de sesion anonima y para investigacion.
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
- Si el API JSON deja de responder con items tras refrescar sesion anonima, la implementacion debe registrar error y no intentar bypass agresivo.

## Verificacion realizada

- Navegador anonimo cargo la URL publica y mostro resultados.
- HTTP directo al HTML devolvio `200` y contuvo el payload de catalogo.
- `page=1` y `page=2` devolvieron paginas distintas con paginacion coherente.
- Endpoint candidato `/api/v2/catalog/items` devolvio `401` sin token.
- Tras bootstrap anonimo por HTML publico, `/api/v2/catalog/items` devolvio `200` con JSON de catalogo.
- `per_page=3`, `5`, `10`, `24` y `96` funcionaron en la observacion.
- Se creo fixture sanitizado para probar mapeo sin datos sensibles reales.
