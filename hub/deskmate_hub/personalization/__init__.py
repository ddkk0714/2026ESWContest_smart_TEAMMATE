"""Optional observation of personalized models; FSM remains authoritative."""
import json
import os
import pkgutil


def load_personalization_config(path=None):
    if path is None:
        try:
            raw = pkgutil.get_data("deskmate_hub", "config/personalization.json")
        except OSError:
            raw = None
        if raw is not None:
            return json.loads(raw.decode("utf-8"))
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "personalization.yaml")
    import yaml
    with open(path, encoding="utf-8") as source:
        return yaml.safe_load(source)


from .runtime import PersonalizationRuntime
