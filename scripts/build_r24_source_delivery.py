"""Compatibility import for historical release scripts; use source_delivery.py."""
import importlib.util
from pathlib import Path


_spec = importlib.util.spec_from_file_location('_pcr_source_delivery',
                                            Path(__file__).with_name('source_delivery.py'))
_delivery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_delivery)
globals().update({name: getattr(_delivery, name) for name in dir(_delivery)
                  if not name.startswith('_')})


if __name__ == '__main__':
    main()
