"""Shared integration constants."""

DOMAIN = "egd_distribuce24"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_EXPORT = "include_export"
CONF_IMPORT_NAME = "import_name"
CONF_EXPORT_NAME = "export_name"
PROFILES = {"C1": ("DCQC", "DSQC"), "A": ("ICQ2", "ISQ2"), "B": ("ICQ2", "ISQ2")}


def profile_name(config, profile):
    """Use a display label without changing entity or statistic identity."""
    key = CONF_EXPORT_NAME if profile in {"DSQC", "ISQ2"} else CONF_IMPORT_NAME
    return config.get(key, "").strip() or profile
