"""Private GUI-owned bot entry: a single stdin request, then Stop/EOF only."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from pcr_maa.labyrinth.owned_runtime import (parse_request, unique_object,
    ConfigurationError, run_owned, public_result)


def main(*, runner=run_owned):
    request=None
    value=None
    line=None
    output=sys.stdout
    def emit(message):
        output.write(json.dumps(message,ensure_ascii=False,separators=(',',':'))+'\n')
        output.flush()
    try:
        line=sys.stdin.readline(65537)
        if len(line)>65536:
            raise ConfigurationError('invalid_configuration')
        value=json.loads(line,object_pairs_hook=unique_object)
        line=None
        request=parse_request(value)
        value=None
        result=runner(request,sys.stdin,emit)
        emit(public_result(result))
        return 0
    except (ConfigurationError, ValueError, TypeError):
        emit(dict(kind='result',outcome='invalid_configuration',batch_complete=False))
        return 2
    except Exception:
        emit(dict(kind='result',outcome='adapter_failed',batch_complete=False))
        return 3
    finally:
        line=None
        if request is not None:
            request.clear()
        if type(value) is dict:
            credentials=value.pop('credentials',None)
            if type(credentials) is dict:
                credentials.clear()


if __name__=='__main__':
    raise SystemExit(main())
