"""Deployment helper preserves the running Compose configuration and touches only API."""
import contextlib,io,json,runpy,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
SCRIPT=Path(__file__).resolve().parents[1]/'tools/update-running-api.py'
class UpdateApiChecks(unittest.TestCase):
 def exercise(self,dry=False,missing=False,turn=False):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);files=[root/'compose.vps.yml',root/'compose.https.yml',root/'compose.calls.yml']
   for file in files:file.write_text('services: {}')
   (root/'custom.env').write_text('SECRET=do-not-print')
   if missing:files[1].unlink()
   labels={'com.docker.compose.project.working_dir':str(root),'com.docker.compose.project':'existing-project',
    'com.docker.compose.service':'komsomol_api','com.docker.compose.project.config_files':','.join(str(f) for f in files),
    'com.docker.compose.project.environment_file':str(root/'custom.env')}
   calls=[]
   def run(command,**kwargs):
    calls.append(command)
    if command[-3:]==['ps','-q','komsomol_turn']:return subprocess.CompletedProcess(command,0,'turn-id','')
    if command==['docker','inspect','turn-id']:return subprocess.CompletedProcess(command,0,json.dumps([{'State':{'Status':'running'},'RestartCount':0}]),'')
    if command[:2]==['docker','inspect']:return subprocess.CompletedProcess(command,0,json.dumps([{'Config':{'Labels':labels}}]),'')
    return subprocess.CompletedProcess(command,0,'','')
   output=io.StringIO()
   with patch('subprocess.run',side_effect=run),patch('sys.argv',[str(SCRIPT)]+(['--dry-run'] if dry else [])+(['--with-turn'] if turn else [])),contextlib.redirect_stdout(output),patch('time.sleep'):
    if missing:
     with self.assertRaises(SystemExit):runpy.run_path(str(SCRIPT),run_name='__main__')
    else:runpy.run_path(str(SCRIPT),run_name='__main__')
   self.assertNotIn('do-not-print',output.getvalue())
   return calls,files
 def test_turn_update_preserves_original_stack(self):
  calls,_=self.exercise(turn=True)
  self.assertEqual(calls[1][-2:],['komsomol_api','komsomol_turn'])
  self.assertIn('--no-deps',calls[1])
  self.assertIn(['docker','inspect','turn-id'],calls)
 def test_dry_run_has_no_mutation(self):
  calls,_=self.exercise(dry=True);self.assertEqual(len(calls),1)
 def test_original_project_files_and_environment_are_preserved(self):
  calls,files=self.exercise();command=calls[1]
  self.assertEqual(command[-6:],['up','-d','--build','--no-deps','--force-recreate','komsomol_api'])
  self.assertEqual(command[command.index('-p')+1],'existing-project')
  self.assertIn('--env-file',command)
  for file in files:self.assertIn(str(file),command)
  self.assertEqual(calls[2][:3],['docker','exec','komsomol_api'])
 def test_missing_configuration_aborts_before_rebuild(self):
  calls,_=self.exercise(missing=True);self.assertEqual(len(calls),1)
if __name__=='__main__':unittest.main(verbosity=2)
