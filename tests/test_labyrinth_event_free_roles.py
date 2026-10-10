from pathlib import Path
from types import SimpleNamespace
import sys,pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from test_labyrinth_scene import setup,finish,frame,offline
from test_labyrinth_event_execution import Journal
from pcr_maa.labyrinth.scene import MaaSceneRuntime
from pcr_maa.labyrinth.character_recognition import MaaCharacters
from pcr_maa.labyrinth.rewards import MaaRoleReward
from pcr_maa.labyrinth.batch import Batch,Binding,BatchStopped

def harness():
 binding=Binding(1,'offline-event-free-replay');seq=[0]
 def stamp(**kw):seq[0]+=1;return dict(verified=True,binding=binding.payload(),frame=seq[0],**kw)
 batch=Batch(SimpleNamespace(goals=(),strategy=None),SimpleNamespace(),Journal(),binding)
 return batch,stamp

def test_run107_free_role_frame_native_scene_and_actual_one_required(tmp_path):
 captured=ROOT/'.runtime/labyrinth-work/runs/1ac20e5938394e67893854ed83ab9c28/acceptance-ending/screen.png'
 if not captured.is_file():pytest.skip('private107 free-role frame excluded from public source')
 _,controller,tasker,token=setup(tmp_path,'free107',[frame(captured)])
 try:
  assert MaaSceneRuntime(tasker).observe().kind=='free_role_reward'
  batch,_=harness();actual,_=MaaCharacters(tasker).read(batch,'opening',stable=False)
  assert actual['count']==0 and actual['required_count']==1 and not actual['selected']
  assert not controller.clicks and not controller.swipes
 finally:finish(tasker,token)

class Characters:
 def __init__(self,count,wrong=False):self.required=count;self.selected=[];self.calls=[];self.wrong=wrong
 def read(self,batch,page,**kw):
  batch.checkpoint()
  if page=='joined':return dict(roles_joined=['wrong'] if self.wrong else self.selected[:]),dict(native_ok=True)
  return dict(count=len(self.selected),required_count=self.required,stable=True,selected=self.selected[:],cards=[dict(character_id=str(i),trusted=True,selected=str(i) in self.selected,box=[i*130,180,130,130]) for i in range(4)],signature='frame'),dict(native_ok=True)
 def run(self,suffix,params,**kw):
  self.calls.append((suffix,params))
  if suffix=='ToggleOnce':self.selected=sorted(self.selected+[params['role_id']])
  elif suffix=='InviteOnce':assert params['required_count']==len(self.selected)==self.required
  else:raise AssertionError(suffix)
  return {},dict(native_ok=True)
 def wait_for(self,batch,page,predicate,**kw):
  value,receipt=self.read(batch,page);assert predicate(value);return value,receipt

@pytest.mark.parametrize('count',[1,2,3])
def test_free_event_reuses_native_select_invite_and_independent_joined(count):
 batch,stamp=harness();characters=Characters(count);profiles={str(i):SimpleNamespace(rating=float(i)) for i in range(4)}
 reward=MaaRoleReward(SimpleNamespace(),stamp,characters=characters,optimizer=SimpleNamespace(profiles=profiles))
 actual=reward.choose_free(batch,1,'30303:free:1',('0',))
 assert actual['roles_joined']==sorted(str(i) for i in range(4-count,4)) and actual['inventory_added']
 assert len(characters.calls)==count+1
 assert all(params['event_free'] for suffix,params in characters.calls if suffix=='ToggleOnce')
 assert sum(x['operation']=='event_free_role_invite' and x['state']=='settled' for x in batch.journal.events())==1

@pytest.mark.parametrize('stop,wrong',[(True,False),(False,True)])
def test_free_event_stop_and_wrong_joined_never_claim_inventory(stop,wrong):
 batch,stamp=harness();batch.stopped=lambda:stop;characters=Characters(1,wrong);reward=MaaRoleReward(SimpleNamespace(),stamp,characters=characters,optimizer=SimpleNamespace(profiles={'1':SimpleNamespace(rating=2)}))
 with pytest.raises(BatchStopped):reward.choose_free(batch,1,'30303:free:1',())
 if stop:assert not characters.calls
 assert not any(x.get('inventory_added') for x in batch.journal.events())