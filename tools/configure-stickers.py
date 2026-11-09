#!/usr/bin/env python3
"""Configure the administrator's Telegram bot without exposing its token."""
import getpass,json,os,re,sys
from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError,URLError
path=Path(__file__).resolve().parents[1]/'.env'
if not path.is_file():sys.exit('Create the existing API .env first.')
secret=getpass.getpass('Token de BotFather (entrada oculta): ').strip()
if not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,128}',secret):sys.exit('Formato de token no válido. No se guardó ningún cambio.')
try:
    with urlopen('https://api.telegram.org/bot'+secret+'/getMe',timeout=15) as response:
        result=json.loads(response.read(65536))
    if not result.get('ok'):sys.exit('Telegram rechazó el token. No se guardó ningún cambio.')
except HTTPError as error:
    sys.exit('Telegram rechazó el token (401).' if error.code==401 else 'Telegram no está disponible. No se guardaron cambios.')
except (URLError,TimeoutError,ValueError):sys.exit('No se pudo conectar con Telegram. No se guardaron cambios.')
text=path.read_text();line='TELEGRAM_STICKER_BOT_TOKEN='+secret
if re.search(r'^TELEGRAM_STICKER_BOT_TOKEN=.*$',text,re.M):
    text=re.sub(r'^TELEGRAM_STICKER_BOT_TOKEN=.*$',lambda _:line,text,flags=re.M)
else:text=text.rstrip('\n')+'\n'+line+'\n'
temp=path.with_name('.env.stickers.tmp');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
try:
    with os.fdopen(fd,'w') as output:output.write(text);output.flush();os.fsync(output.fileno())
    os.replace(temp,path)
finally:
    if temp.exists():temp.unlink()
print('Token verificado y guardado. Reinicia la API incluyendo compose.stickers.yml. El token no se ha mostrado.')
