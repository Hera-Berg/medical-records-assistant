"""Disposable browser-test server: never mutates the active Jane demo."""
import os
from pathlib import Path
import tempfile
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import uvicorn
from agent import device
from agent.demo.jane import PROFILE
from agent.demo.calendar_data import CARE_TEAM
from agent.demo.review_dataset import populate
from agent.clinician_auth import provision,store_path,password_hash
from agent.server import create_app

if not os.environ.get('CARE_DEMO_PASSWORD'):raise SystemExit('Supply CARE_DEMO_PASSWORD for this disposable fixture.')
root=Path(tempfile.mkdtemp(prefix='care-review-browser-'))
os.environ[device.ENV_CONFIG_HOME]=str(root/'machine')
os.environ[device.ENV_DEVICE]='review-browser-fixture'
vault=populate(root/'vault',PROFILE,CARE_TEAM)
if os.environ.get('CAREMIND_DOCUMENT_FIXTURE')=='1':
    from agent.events import envelope
    from agent.demo.specialist_letters import upgrade as specialist_letters
    from agent.demo.document_editions import upgrade as facility_documents
    vault.append(envelope.new('note.recorded',vault.identity.id,payload={'demo_scenario':'jane-two-year-v1','care_team':[*CARE_TEAM,{'name':'Daniel Brooks','role':'Podiatrist','organisation':'Example Community Health'},{'name':'Olivia Reed','role':'Audiologist','organisation':'Example Community Audiology'}]}))
    specialist_letters(vault)
    facility_documents(vault,export=root/'exports')
provision(vault.root)
path=store_path(vault.root);data=json.loads(path.read_text())
for account in data['accounts'].values():account['password']['hash']=password_hash(os.environ['CARE_DEMO_PASSWORD'],account['password']['salt'])
path.write_text(json.dumps(data))
print('Disposable fixture: '+str(root),flush=True)
uvicorn.run(create_app(vault,worker=False),host='127.0.0.1',port=7788,log_level='warning')
