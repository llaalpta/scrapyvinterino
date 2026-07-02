# Investigacion Catalogo Vinted

Fecha de observacion: 2026-07-02.

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

## Parametros y paginacion

- La URL publica usa arrays como `catalog[]=76` y `brand_ids[]=88&brand_ids[]=364`.
- Algunos endpoints auxiliares convierten esos filtros a `catalog_ids=76` y `brand_ids=88,364`.
- Para el HTML de catalogo, la URL publica se puede conservar como fuente de verdad.
- `page=2` en la URL publica devolvio otra pagina con `pagination.current_page=2`.
- El payload observado contenia:
  - `current_page`;
  - `total_pages`;
  - `total_entries`;
  - `per_page`;
  - `time`.
- Con la URL de prueba se observaron 96 items por pagina.

## Contrato HTTP minimo observado

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
- La primera implementacion real debe usar HTTP directo al HTML publico y parsear el stream inicial de Next.
- Playwright queda reservado para investigacion o para obtener contexto anonimo si en el futuro cambia el comportamiento.

## Sanitizacion

- No guardar cookies, tokens, cabeceras de sesion, IDs de usuario reales, direcciones ni datos de pago.
- Los fixtures deben usar valores sinteticos o sanitizados.
- No guardar parametros de tracking completos, `search_tracking_params`, URLs de perfil ni payloads de usuario completos.
- Guardar solo el subconjunto necesario para probar mapeo.

## Fallos y riesgos

- El endpoint JSON de catalogo puede existir, pero no fue accesible sin autenticacion en esta observacion.
- El stream de Next es una estructura interna y puede cambiar.
- DataDome/captcha puede aparecer segun IP, frecuencia o entorno.
- La disponibilidad de campos opcionales no esta garantizada; el parser debe tolerar campos ausentes.
- Si el HTML deja de incluir items, la implementacion debe registrar error y no intentar bypass agresivo.

## Verificacion realizada

- Navegador anonimo cargo la URL publica y mostro resultados.
- HTTP directo al HTML devolvio `200` y contuvo el payload de catalogo.
- `page=1` y `page=2` devolvieron paginas distintas con paginacion coherente.
- Endpoint candidato `/api/v2/catalog/items` devolvio `401` sin token.
- Se creo fixture sanitizado para probar mapeo sin datos sensibles reales.
