"""Opt-in lifecycle for explicitly configured local personalization files."""
import json
import os
import pkgutil


def load_privacy_config():
    try:
        raw = pkgutil.get_data("deskmate_hub", "config/privacy.json")
    except OSError:
        raw = None
    if raw is not None:
        return json.loads(raw.decode("utf-8"))
    import yaml
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "privacy.yaml")
    with open(path, encoding="utf-8") as source:
        return yaml.safe_load(source)


class PersonalizationPrivacy:
    def __init__(self, config, *, baseline_file=None, on_reset=None, on_grant=None):
        self.config = config
        self.enabled = config.get("enabled") is True
        self.policy_ready = (config.get("policy_approved") is True
                             and isinstance(config.get("policy_version"), str)
                             and config["policy_version"] not in ("", "pending"))
        self.consented = False
        self.last = {}
        self.boot_id = None
        self.recent_requests = []
        self.on_reset = on_reset or (lambda: None)
        self.on_grant = on_grant or (lambda: None)
        registered = config.get("personal_model_files", [])
        self.files = ([baseline_file] if baseline_file else []) + (registered if isinstance(registered, list) else [])
        self.recent_limit = config.get("recent_request_limit", 64)
        self.valid = False
        if not self.enabled:
            return
        try:
            if (not isinstance(registered, list) or type(self.recent_limit) is not int or self.recent_limit < 1
                    or not isinstance(config.get("data_root"), str) or not config["data_root"]):
                raise ValueError("invalid privacy configuration")
            self.root = os.path.abspath(config["data_root"])
            self.consent_file = config["consent_file"]
            self._path(self.consent_file)
            for path in self.files:
                if self._path(path) == self._path(self.consent_file):
                    raise ValueError("consent record overlaps personal data")
            self.valid = True
            if self.policy_ready:
                with open(self._path(self.consent_file), encoding="utf-8") as source:
                    state = json.load(source)
                self.consented = (state.get("consented") is True
                                  and state.get("policy_version") == config["policy_version"])
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def _path(self, path):
        if not isinstance(path, str) or not path:
            raise ValueError("invalid registered path")
        absolute = os.path.abspath(path)
        root = self.root
        if (os.path.commonpath([root, absolute]) != root or absolute == root
                or os.path.realpath(root) != root or os.path.realpath(absolute) != absolute):
            raise ValueError("registered path escapes managed root or uses a symlink")
        if os.path.isdir(absolute):
            raise ValueError("directories cannot be deleted")
        return absolute

    def _save(self):
        path = self._path(self.consent_file)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        temporary = self._path(path + ".tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({"consented": self.consented, "policy_version": self.config.get("policy_version", "pending")}, output)
            os.replace(temporary, path)
        finally:
            if os.path.isfile(temporary):
                os.unlink(temporary)

    def handle(self, command):
        rid = command.get("request_id")
        action = command.get("action")
        allowed = {"kind", "request_id", "action", "policy_version", "confirmed", "hub_boot_id"}
        if (not isinstance(rid, str) or not 1 <= len(rid) <= 128
                or action not in ("grant", "revoke", "delete") or set(command) - allowed
                or command.get("confirmed") is not True):
            return
        if rid == self.last.get("request_id"):
            return
        replayed = rid in self.recent_requests
        self.last = {"request_id": rid, "action": action, "status": "failed", "error": None}
        if not self.enabled or not self.valid:
            self.last["error"] = "unavailable"
            return
        if replayed or (self.boot_id is not None and command.get("hub_boot_id") != self.boot_id):
            self.last["error"] = "stale_request"
            return
        self.recent_requests.append(rid)
        self.recent_requests = self.recent_requests[-self.recent_limit:]

        if action == "grant" and (not self.policy_ready or command.get("policy_version") != self.config["policy_version"]):
            self.last["error"] = "policy_not_approved"
            return
        try:
            if action == "grant":
                self.consented = True
                try:
                    self._save()
                except (OSError, ValueError):
                    self.consented = False
                    self.on_reset()
                    raise
                self.on_grant()
            else:
                # Disable in-memory use before touching disk. Partial deletion must never report success.
                self.consented = False
                self.on_reset()
                self._save()
                failed = False
                for registered in self.files:
                    try:
                        path = self._path(registered)
                        if os.path.lexists(path):
                            os.unlink(path)
                    except (OSError, ValueError):
                        failed = True
                if failed:
                    raise OSError("some registered files could not be deleted")
            self.last["status"] = "succeeded"
        except (OSError, ValueError):
            self.last["error"] = "storage_error"

    def snapshot(self):
        return {"hub_boot_id": self.boot_id, "available": self.enabled and self.valid, "policy_ready": self.policy_ready,
                "policy_version": self.config.get("policy_version", "pending"),
                "consented": self.consented, "registered_model_files": len(self.config.get("personal_model_files") or []) if isinstance(self.config.get("personal_model_files", []), list) else 0,
                **self.last}
