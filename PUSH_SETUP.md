# Activar notificaciones Android (Firebase Cloud Messaging)

Firebase debe pertenecer al mismo proyecto en Android y en el servidor. Instalar el APK
sin esa configuración no permite recibir avisos con la app cerrada.

## 1. Proyecto y Android

1. Abre https://console.firebase.google.com/ y crea un proyecto llamado Komsomolgram.
   Google Analytics es opcional para este flujo.
2. Añade una aplicación Android con el identificador exacto `ru.komsomol.gram`.
3. Descarga `google-services.json`. Colócalo en `Komsomolgram/app/google-services.json`
   (al lado de `app/build.gradle.kts`, fuera de `src`).
4. Haz Sync en Android Studio y genera e instala de nuevo el APK. Inicia sesión y
   acepta el permiso de notificaciones. El plugin ya se aplica automáticamente cuando
   encuentra el archivo. No necesitas integrar Analytics ni añadir más dependencias.

Los teléfonos deben disponer de Google Play Services compatibles para este transporte.

## 2. Credencial del servidor

En la configuración del MISMO proyecto Firebase, abre Cuentas de servicio → Firebase
Admin SDK → Generar nueva clave privada. Es un archivo diferente de google-services.json.
La clave privada se coloca solo en el VPS; nunca en Android ni en GitHub.

Crea el directorio en la sesión SSH del servidor:

```bash
install -d -m 700 /opt/komsomolgram-secrets
```

Desde PowerShell en tu PC, copia el archivo descargado (sustituye la ruta local):

```powershell
scp "C:\Users\corpo\Downloads\tu-clave-firebase.json" root@46.173.218.131:/opt/komsomolgram-secrets/firebase-admin.json
```

De vuelta en la sesión SSH:

```bash
chmod 600 /opt/komsomolgram-secrets/firebase-admin.json
cd /opt/komsomolgram-backend
git pull --ff-only origin main
docker compose -f compose.vps.yml -f compose.push.yml up -d --build
docker compose -f compose.vps.yml -f compose.push.yml logs --tail=80 komsomol_api
```

`compose.push.yml` ya está incluido en el repositorio y monta la clave en modo lectura,
añadiendo FIREBASE_CREDENTIALS al contenedor. No hace falta editar .env para esta opción.
Si utilizas además `compose.https.yml`, conserva ese `-f compose.https.yml` antes de
`-f compose.push.yml` en tus comandos de despliegue habituales. No elimines volúmenes.

El log debe mostrar `Firebase push initialized`. Si falta la clave, no se configura
silenciosamente: el log lo indica y Ajustes muestra que falta configurar el servidor.
No copies credenciales, tokens ni archivos privados al chat para diagnosticarlo.

## 3. Prueba desde la app

Tras instalar la app configurada, abre Ajustes → Notificaciones.

- Comprueba el permiso de Android, la configuración de la app, la del servidor y el registro
  del dispositivo. Reintentar vuelve a obtener el token y a registrarlo.
- La prueba local comprueba que Android puede mostrar el aviso y reproducir el sonido.
- La prueba desde el servidor comprueba la ruta real de Firebase. El envío aceptado indica
  que Firebase lo recibió, no garantiza que Android ya lo haya mostrado. Hay un límite
  de una prueba cada 30 segundos por cuenta.
- Usa dos cuentas. Deja una app en segundo plano, bloquea el teléfono y envía texto, foto,
  audio y zumbido desde la otra. Tocar el aviso abre el chat. Comprueba también silencio
  de contactos/grupos y cierre de sesión.

Quitar la app de recientes y forzar su detención son acciones distintas. Android bloquea
FCM tras Forzar detención hasta que se abra la app otra vez. Los ajustes de batería del
fabricante y el modo No molestar también pueden afectar la entrega y el sonido.

El payload normal contiene identificadores y el nombre del remitente, sin el texto ni
los adjuntos del mensaje. La vista previa visible es genérica.

## Comprobaciones del código

`python tests/check_push.py` con requirements-dev.txt comprueba con Firebase simulado:
registro y propiedad de dispositivos, diagnóstico de configuración, prueba autenticada,
límite de frecuencia, selección de destinatarios, prioridad y limpieza de tokens inválidos.
La prueba real requiere las credenciales del proyecto y un teléfono configurado.
