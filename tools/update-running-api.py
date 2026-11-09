#!/usr/bin/env python3
"""Rebuild only the existing API using its original Docker Compose configuration."""
import argparse,json,subprocess,sys,time
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container',default='komsomol_api')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    def run(command,**kwargs):
        return subprocess.run(command,check=True,text=True,**kwargs)
    try:
        info=json.loads(run(['docker','inspect',args.container],capture_output=True).stdout)[0]
        labels=info.get('Config',{}).get('Labels',{}) or {}
        directory=labels.get('com.docker.compose.project.working_dir')
        project=labels.get('com.docker.compose.project')
        service=labels.get('com.docker.compose.service')
        files=labels.get('com.docker.compose.project.config_files','').split(',')
        if not directory or not project or not service or not all(files):
            sys.exit('No se pudo identificar la configuración Compose original. No se cambió nada.')
        directory=Path(directory)
        paths=[Path(f) if Path(f).is_absolute() else directory/f for f in files]
        if not directory.is_dir() or not all(p.is_file() for p in paths):
            sys.exit('Faltan archivos de la configuración Compose original. No se cambió nada.')
        command=['docker','compose','--project-directory',str(directory),'-p',project]
        env_files=labels.get('com.docker.compose.project.environment_file','')
        if env_files:
            for value in env_files.split(','):
                path=Path(value) if Path(value).is_absolute() else directory/value
                if not path.is_file():sys.exit('Falta un archivo de entorno original. No se cambió nada.')
                command+=['--env-file',str(path)]
        for path in paths:command+=['-f',str(path)]
        command+=['up','-d','--build','--no-deps','--force-recreate',service]
        print('API:',service,'· proyecto:',project)
        print('Se conservan los archivos Compose y de entorno originales. Solo se recrea la API.')
        if args.dry_run:
            print('Revisión completada. No se ejecutó la actualización.');return
        run(command,cwd=directory)
        check="import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/openapi.json',timeout=3)); assert '/api/v1/basketball/status' in d['paths'], 'Backend anterior sin basket'"
        for _ in range(30):
            result=subprocess.run(['docker','exec',args.container,'python','-c',check],capture_output=True,text=True)
            if result.returncode==0:
                print('Backend actualizado: las rutas de básquet están disponibles.');return
            time.sleep(1)
        sys.exit('La API no confirmó la nueva ruta. Revisa los logs del contenedor; no se borró ningún volumen.')
    except (OSError,subprocess.CalledProcessError,ValueError,KeyError,IndexError):
        sys.exit('No se pudo actualizar la API. Revisa Docker y los archivos Compose originales.')

if __name__=='__main__':main()
