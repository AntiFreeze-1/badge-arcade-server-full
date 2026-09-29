"""The Nintendo (and Pretendo) hosts the Badge Arcade addon handles.

Shared by the mitmproxy addon and hotspot.py, which points exactly these names
at the PC in hotspot mode. No dependencies, so both Pythons can import it.
"""

NASC_HOSTS = {"nasc.nintendowifi.net", "nasc.pretendo.cc"}
NPDL_HOSTS = {"npdl.cdn.nintendowifi.net", "npdl.cdn.pretendo.cc"}
ACCOUNT_HOSTS = {"account.nintendo.net", "account.pretendo.cc"}
NPPL_HOSTS = {"nppl.c.app.nintendowifi.net", "nppl.app.nintendo.net", "nppl.c.app.pretendo.cc", "nppl.app.pretendo.cc"}

HANDLED_HOSTS = NASC_HOSTS | NPDL_HOSTS | ACCOUNT_HOSTS | NPPL_HOSTS
