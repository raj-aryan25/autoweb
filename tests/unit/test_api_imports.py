"""The server extra does not change the package's explicit initialization boundary."""

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")


def test_import_and_app_factory_start_no_clients_or_runtime_storage(tmp_path):
    script = '''
import importlib.abc
import sys
from pathlib import Path
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'playwright', 'google', 'google_auth_oauthlib'} or fullname == 'autoweb.ai.client':
            raise AssertionError('Client imported: ' + fullname)
def audit(event, args):
    if event in {'socket.connect', 'socket.bind', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('Resource startup: ' + event)
    if event == 'open' and isinstance(args[0], str) and args[0].endswith(('.env', 'settings.json', 'token.json', 'credentials.json')):
        raise AssertionError('Runtime configuration read')
sys.meta_path.insert(0, Guard())
sys.addaudithook(audit)
from autoweb_api.api import create_app
from autoweb_api.settings import ServiceSettings
import autoweb_api.cli
settings = ServiceSettings(Path(sys.argv[1]))
create_app(settings)
assert not settings.data_directory.exists()
'''
    subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=True, cwd=Path(__file__).resolve().parents[2])
