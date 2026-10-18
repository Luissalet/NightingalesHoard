"""Native calculation module; shares Nightingale's family SDK."""
__version__ = "0.1.0"
import importlib.util
from pathlib import Path
import sys

# Load the same bundled SDK source in a separate namespace. The family SDK keeps
# per-app identity/token state at module scope, so sharing its module object would
# let enabling Data replace Calculation's token and identity.
sdk_path = Path(__file__).parents[1] / "nightingale" / "hoard_link"
sdk_name = __name__ + ".hoard_link"
if sdk_name not in sys.modules:
    spec = importlib.util.spec_from_file_location(sdk_name, sdk_path / "__init__.py", submodule_search_locations=[str(sdk_path)])
    sdk = importlib.util.module_from_spec(spec)
    sys.modules[sdk_name] = sdk
    spec.loader.exec_module(sdk)
