# Stickers: configuración e importación

Los paquetes viven en el teléfono. El backend solo transfiere los archivos de Telegram
en memoria mientras se importan; no mantiene una biblioteca de stickers en disco o BD.

## Archivos de WhatsApp y WebP

En el chat: botón de stickers junto al campo de texto → **+ → Importar .wastickers o ZIP**.
También se puede compartir un archivo WebP o un paquete ZIP/.wastickers con Komsomolgram.
La app confirma la importación antes de guardarlo.

- Se admiten paquetes ZIP con WebP/PNG y el manifiesto habitual de WhatsApp/Sticker Maker.
- Las imágenes se ajustan a un lienzo transparente de 512 × 512 sin deformarlas.
- Los WebP animados cuadrados se conservan. Android 9+ reproduce su animación.
- Favoritos y recientes son copias locales. Mantener pulsado añade/quita favoritos.
- El menú de un sticker recibido permite guardarlo sin importar el paquete completo.
- La app no puede leer el almacenamiento privado de WhatsApp. Hace falta un archivo
  exportado/compartido por el creador o una app de paquetes; un enlace privado de WhatsApp
  no equivale a un archivo de stickers importable.

## Enlaces de Telegram: una configuración del administrador

La API oficial de Telegram requiere un token de bot para getStickerSet y getFile.
No se necesitan cuentas ni tokens de Telegram en los teléfonos de los usuarios.

1. Crea un bot con **@BotFather** en Telegram y copia su token.
2. En el VPS, abre /opt/komsomolgram-backend/.env y añade:

       TELEGRAM_STICKER_BOT_TOKEN=el_token_del_bot

   No subas ese archivo ni el token a GitHub.

3. Actualiza y reinicia con el archivo de Compose adicional:

       git pull --ff-only origin main
       docker compose -f compose.vps.yml -f compose.https.yml -f compose.push.yml -f compose.stickers.yml up -d --build

4. En la app: stickers → **+ → Añadir desde Telegram**, pega
   https://t.me/addstickers/NOMBRE_DEL_PAQUETE y pulsa Importar.

Los stickers WebP estáticos se importan completos. Telegram usa TGS y WebM para
otros stickers animados: esta implementación importa su miniatura estática y lo avisa
antes de importar; no convierte esas animaciones. Los packs y favoritos siguen funcionando
sin conexión después de importarse.

Si falta el token, la app explica que el administrador debe configurarlo. Importar
archivos sigue funcionando sin ese token.

## Controles de transferencia

- Solo usuarios autenticados; enlaces de t.me/addstickers o telegram.me/addstickers.
- El token se queda en el servidor y no se incluye en respuestas ni registros HTTP.
- Los tickets de descarga están firmados, vinculados al usuario y caducan a los 15 minutos.
- Máximo 120 elementos de Telegram por importación y 512 KiB por descarga.
- Los ZIP se limitan a 400 entradas y 32 MiB; se rechazan rutas con traversal.
- Los paquetes normalizados se limitan a 32 MiB en el teléfono.
