# Canción favorita con un solo enlace

En el perfil propio se pega un enlace de Yandex Music, Spotify o YouTube Music.
La app busca automáticamente título, artista y portada y presenta la tarjeta antes
de guardar. No hay campos manuales de título/artista. Se puede ocultar sin perder
la configuración o quitar la canción por completo.

El visitante ve portada, título, artista y logo del servicio. Tocar abre la app
musical correspondiente si está instalada; en otro caso se usa el enlace HTTPS.
No se descargan canciones ni se introduce reproducción de audio en Komsomolgram.

## Backend

- POST /api/v1/auth/me/favorite-song/preview recibe {url} y devuelve metadatos.
- GET /api/v1/auth/me/favorite-song devuelve la configuración privada del dueño.
- PUT /api/v1/auth/me/favorite-song conserva el contrato previo y admite artwork_url.
- Las respuestas públicas omiten la canción cuando el propietario la oculta.

Spotify utiliza oEmbed y los metadatos públicos de su página para obtener también
el artista; oEmbed por sí solo no siempre lo incluye. Se aceptan enlaces compartidos
spotify.link mediante el propio oEmbed de Spotify. YouTube utiliza oEmbed y el nombre
del canal como autor; si el canal es «Artista - Topic» se elimina ese sufijo.
Yandex utiliza el recurso público de pistas de su iframe y como alternativa sus
metadatos públicos Open Graph. Su disponibilidad depende del acceso regional del
servidor; si el proveedor lo bloquea no se inventan datos ni se guarda una tarjeta vacía.

No se necesitan tokens musicales. Estos servicios pueden restringir los metadatos
o cambiar sus respuestas. La app muestra un error con reintento en ese caso.

Las peticiones están limitadas a orígenes fijos, 1,5 MiB por respuesta, timeout de
8 segundos por petición y cuatro resoluciones simultáneas. La caché en memoria
guarda como máximo 256 resultados públicos durante seis horas. No se guarda audio
ni la imagen de portada en el servidor: solo texto y la URL de un CDN permitido.

Android descarga portadas desde esos CDN sin enviar tokens de Komsomolgram. Limita
cada respuesta a 512 KiB, reduce la imagen a 256 px y reutiliza caché de memoria de
4 MiB y disco de 16 MiB. Si no hay portada se muestra el logo del servicio.

## Recursos visuales

- Iconos Spotify y YouTube Music: SVG de Simple Icons (CC0), usados para identificar
  sus servicios. https://github.com/simple-icons/simple-icons
- Símbolo Yandex Music: recurso SVG de la página oficial del nuevo branding.
  https://music.yandex.ru/brand/
- Los iconos vectoriales se incluyen en Android; no se consultan apps instaladas
  para conseguirlos ni hace falta permiso de inventario de aplicaciones.

## Comprobaciones

tests/check_song_preview.py comprueba los tres proveedores con respuestas acotadas,
enlaces cortos de Spotify, caché, privacidad, errores, redirecciones y URLs inválidas.
tests/check_favorite_song.py conserva las pruebas de guardar, ocultar y eliminar.
Se probaron además Spotify y YouTube con metadatos reales. Yandex devolvió una
restricción regional en el entorno de desarrollo: comprobarlo desde el VPS antes
de afirmar que su catálogo está accesible desde ese servidor.

Desplegar backend antes de distribuir el nuevo cliente. No hay migración adicional
de BD en esta revisión: la portada se guarda dentro del JSON de canción ya existente.
La revisión anterior sí añadía favorite_song/show_favorite_song y su migración de inicio.
