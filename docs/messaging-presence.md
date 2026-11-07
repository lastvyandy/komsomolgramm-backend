# Respuestas, lectura y presencia

Actualizar este backend antes de instalar el APK nuevo. Al iniciar, el servicio
crea la tabla `comrade_presence` y añade `dispatches.reply_to_id` de forma
idempotente. Se conservan los mensajes existentes. No borrar volúmenes de Docker.

Después de actualizar los archivos en el VPS, reconstruir y reiniciar con la
configuración de despliegue que ya se utiliza:

```bash
docker compose -f compose.vps.yml up -d --build
docker compose -f compose.vps.yml logs --tail=80 komsomol_api
curl -sS http://127.0.0.1:8000/health
```

## Comportamiento

- Responder: el cliente envía `reply_to_id` por WebSocket o como query parameter
  al subir un adjunto. El servidor comprueba que el original pertenece al mismo
  chat y no está eliminado. El historial y los eventos incluyen una cita breve.
  Las citas se vuelven a resolver al cargar el historial; al eliminar el original
  dejan de mostrar su contenido. El acceso al contexto también exige membresía.
- Lectura: se reutilizan los cursores persistentes existentes. El endpoint de
  lectura publica un evento `read`. `GET /dispatches/{id}/readers` permite
  recuperar el estado al abrir el chat o reconectar. Los cursores no retroceden.
  Android usa una marca para enviado y dos cuando todos los otros miembros han
  leído el mensaje. En conversaciones privadas esto equivale al destinatario.
- Escritura: los eventos `typing` se limitan por frecuencia y no se guardan.
  El cliente envía parada al dejar de escribir o salir, y elimina indicadores
  que no se renuevan en seis segundos. El servidor también publica parada al
  desconectarse el socket.
- Historial: `before_id` pagina los mensajes anteriores usando un cursor del
  mismo chat, por lo que los mensajes nuevos no desplazan el punto de lectura.
- Grupos: los miembros pueden añadir hasta 50 camaradas activos por solicitud
  con `POST /soviets/{id}/members`. Los chats privados rechazan cambios de
  miembros y repetir una solicitud no duplica miembros.
- Zumbido: evento WebSocket autenticado, transitorio y no persistido; se limita
  a uno cada cinco segundos por conexión.
- Presencia: mientras la app está en primer plano, renueva una sesión cada
  veinte segundos. La sesión vence a los cuarenta y cinco segundos si la app
  pierde conexión. Al salir envía una parada. Las sesiones se contabilizan por
  separado para admitir varios dispositivos. La última actividad se guarda en
  PostgreSQL; las sesiones temporales usan Redis. El estado consultado por el
  cliente se actualiza aproximadamente cada cinco segundos.
- Privacidad: Ajustes → Privacidad permite ocultar tanto el estado en línea como
  la última conexión. La restricción se aplica en la API y es recíproca: quien
  oculta su actividad tampoco obtiene la actividad exacta de los demás. El
  aviso de escritura y las confirmaciones de lectura son independientes de esa
  opción.

## Validación

```bash
python -m pip install -r requirements-dev.txt
python tests/check_messaging.py
```

La comprobación utiliza una base SQLite temporal, Redis simulado y transporte
de push desactivado. Prueba permisos de respuestas y archivos, historial con
cursor, grupos, contexto, citas eliminadas, contactos por cuenta, cursores de
lectura que no retroceden, varias sesiones, privacidad y eventos WebSocket de
escritura, zumbido y respuesta. No utiliza la base de datos del VPS. No sustituye una comprobación
del despliegue con PostgreSQL y Redis reales.

En Android, probar con dos cuentas: escritura/parada, respuesta a texto y
multimedia, cita de un mensaje antiguo, paso de una a dos marcas al abrir el
destinatario y cambios de privacidad. Repetir pasando una app a segundo plano,
cerrándola y desconectando su red. Probar también ruso y español.
