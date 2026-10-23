import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]

class DataAdminTests(unittest.TestCase):
    def test_new_environment_is_empty_isolated_and_restartable(self):
        original=(ROOT/'.env').read_bytes() if (ROOT/'.env').exists() else None
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'data';output=root/'output'
            command=[sys.executable,'scripts/data_admin.py','init','--data-dir',str(data),'--output-dir',str(output)]
            run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stderr)
            result=json.loads(run.stdout);self.assertEqual(result['courses'],0);self.assertEqual(result['recordings'],0)
            from web.backend.runtime_config import CATALOG
            env={key:value for key,value in os.environ.items() if key not in CATALOG}
            env['LECORDER_ENV_FILE']=result['environment']
            program="from web.backend.routes import create_app;a=create_app();d=a.test_client().get('/api/bootstrap').json;assert not d['courses'] and not d['recordings'];print(d['dataset_id']);a.extensions['lecorder_services'].close()"
            restart=subprocess.run([sys.executable,'-c',program],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(restart.returncode,0,restart.stderr)
            self.assertEqual(restart.stdout.strip(),result['dataset_id'])
            before=(data/'lecorder.db').read_bytes()
            refused=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
            self.assertNotEqual(refused.returncode,0);self.assertEqual((data/'lecorder.db').read_bytes(),before)
            status=subprocess.run([sys.executable,'scripts/data_admin.py','status',str(data/'lecorder.db')],cwd=ROOT,capture_output=True,text=True,timeout=30)
            self.assertEqual(json.loads(status.stdout)['integrity'],'ok')
        if original is not None:self.assertEqual((ROOT/'.env').read_bytes(),original)
