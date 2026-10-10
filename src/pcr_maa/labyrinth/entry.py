"""Read-only entrance navigation and identity verification via Maa Pipeline.

Never taps 出发/继续挑战. This module alone does not qualify automatic play.
"""
from .. import native

def observe_home(tasker):
    receipt=native.run(tasker,'LabyHomeObserved')
    receipt['page']='labyrinth_home' if receipt['native_ok'] and any(n['name']=='LabyHomeObserved' and n['completed'] for n in receipt['nodes']) else 'unknown'
    return receipt

def open_from_adventure(tasker):
    receipt=native.run(tasker,'LabyDawnEntryClick',reset_hits=('LabyDawnEntryClick',))
    receipt['page']='labyrinth_home' if receipt['native_ok'] and any(n['name']=='LabyHomeObserved' and n['completed'] for n in receipt['nodes']) else 'unknown'
    return receipt
