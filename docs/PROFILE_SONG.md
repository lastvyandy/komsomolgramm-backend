# Canción favorita y nombres de Consejos

El canal RU se llama **Объявления Верховного Совета RU** y el ES mantiene
**Avisos del soviet supremo ES**. La inicialización corrige también los nombres
de los dos canales existentes, conservando identificadores, mensajes y publicador.

## Canción en el perfil

En el perfil propio, la tarjeta **Canción favorita** permite añadir título,
artista y enlace completo compartido desde Yandex Music, Spotify o YouTube Music.
El servicio se reconoce por el enlace; no se descargan audios ni se consultan
servicios externos para resolver metadatos. El título y artista se escriben en
el formulario. No se requieren claves de Spotify/Yandex ni otra suscripción
para guardar el campo en Komsomolgram.

El visitante ve una tarjeta con título, artista y servicio. Al tocarla se intenta
abrir la aplicación musical correspondiente; si no está disponible, Android abre
el enlace HTTPS con su controlador habitual. La disponibilidad de reproducción
y suscripciones pertenece a cada servicio. Un enlace abre el servicio elegido
por el propietario, no busca automáticamente equivalencias en los otros catálogos.

Se puede ocultar sin perder los datos o quitar completamente. El servidor omite
la canción oculta de todas las respuestas públicas de usuario; solo el endpoint
privado de configuración del propietario conserva la información para editar.

Rutas (bajo /api/v1):

- GET /auth/me/favorite-song: configuración privada.
- PUT /auth/me/favorite-song: {song: {title, artist, url} | null, visible: bool}.
- Las respuestas públicas de usuario incluyen favorite_song o null.

Solo se admiten enlaces completos a pistas, no playlists, perfiles ni acortadores
de terceros. YouTube y youtu.be se normalizan a YouTube Music. Se eliminan parámetros
de seguimiento de los enlaces guardados. No se resuelven redirecciones ni se descargan
portadas desde URLs introducidas por el usuario.

El inicio añade favorite_song JSON y show_favorite_song BOOLEAN a comrades. Desplegar
backend antes del cliente. Los perfiles sin canción siguen funcionando como antes.

Validación: tests/check_favorite_song.py cubre enlaces, privacidad, eliminación,
autenticación y reparación de nombres de Consejos. Probar además en teléfonos la
apertura de cada app musical y su alternativa cuando no esté instalada.
