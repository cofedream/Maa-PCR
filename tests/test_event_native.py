"""Test-only frame loader copied from the historical event helper; no collected cases."""
from pathlib import Path
import pytest
from pcr_maa import native
LIVE=Path(__file__).resolve().parents[1]/'.runtime/private-fixtures/hermes-pcr/live'
def frame(name):
    path = LIVE/(name+'.png')
    if not path.is_file():
        pytest.skip('private saved-frame fixture is not distributed: ' + name)
    return native.read_image(path)
