"""Load the HA-independent client without importing the integration entry point."""

import sys
import types
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "egd_distribuce24"
package = types.ModuleType("egd_client_test")
package.__path__ = [str(PACKAGE)]
sys.modules["egd_client_test"] = package
