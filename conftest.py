"""Put the repo root on sys.path so tests can import the `apps` namespace
package (the FastAPI app lives at apps/api, which isn't an installed package).
The `soundseek` core is installed (editable) and imports without this."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
