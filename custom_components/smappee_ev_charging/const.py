"""Constants voor de Smappee Custom integratie."""
import json
from pathlib import Path
from typing import Final

MANUFACTURER: Final = "Smappee"
CONFIGURATIN_URL: Final = "https://dashboard.smappee.net"

ATTRIBUTION: Final = f"Data provided by {MANUFACTURER}"

manifestfile = Path(__file__).parent / "manifest.json"
try:
    with open(manifestfile) as json_file:
        manifest_data = json.load(json_file)
    
    NAME = manifest_data.get("name", "Smappee Charger")
    VERSION = manifest_data.get("version", "1.0.0")
    ISSUEURL = manifest_data.get("issue_tracker", "")
    DOMAIN = manifest_data.get("domain", "smappee_ev_charger")
except Exception:
    NAME = "Smappee Charger"
    VERSION = "1.0.0"
    ISSUEURL = ""
    DOMAIN = "smappee_ev_charger"

STARTUP = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom component
If you have any issues with this you need to open an issue here:
{ISSUEURL}
-------------------------------------------------------------------
"""