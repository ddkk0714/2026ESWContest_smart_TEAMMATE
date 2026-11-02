"""Generate ONLY PoC policy/header from reviewed non-secret account bindings.

No board operation. Default is unconfigured, no .conf. Never infer names/UIDs.
"""

import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

SERVICE = "com.deskmate.thinqpoc1"
PATH = "/com/deskmate/thinqpoc1"
KEYS = {
    "service_user",
    "ui_user",
    "operator_user",
    "reviewed",
    "read_only_verified",
    "manual_verified",
}


def render(config):
    if set(config) != KEYS or config["reviewed"] is not True:
        raise ValueError("unreviewed_bindings")
    for key in ("service_user", "ui_user"):
        if not isinstance(config[key], str) or not re.fullmatch(
            r"u0_a[0-9]+", config[key]
        ):
            raise ValueError("invalid_package_account")
    op = config["operator_user"]
    if (
        not isinstance(op, str)
        or op == "root"
        or not re.fullmatch(r"[a-z_][a-z0-9_-]*", op)
    ):
        raise ValueError("invalid_operator")
    if len({config["ui_user"], config["service_user"], op}) != 3:
        raise ValueError("roles_must_be_separate")
    if any(
        type(config[k]) is not bool for k in ("read_only_verified", "manual_verified")
    ):
        raise ValueError("invalid_flags")
    if config["manual_verified"] and not config["read_only_verified"]:
        raise ValueError("manual_requires_read_validation")
    service, ui, operator = (
        escape(config[k]) for k in ("service_user", "ui_user", "operator_user")
    )
    policy = f"""<!-- Generated for ONLY {SERVICE}; never install before mapping/profile review. -->
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy context="default">
    <deny own="{SERVICE}"/>
    <deny send_destination="{SERVICE}" send_type="method_call"/>
  </policy>
  <policy user="{service}"><allow own="{SERVICE}"/></policy>
  <policy user="{ui}">
    <allow send_destination="{SERVICE}" send_type="method_call" send_path="{PATH}" send_interface="{SERVICE}" send_member="Exchange"/>
    <allow send_destination="{SERVICE}" send_type="method_call" send_path="{PATH}" send_interface="{SERVICE}" send_member="AuthStatus"/>
  </policy>
  <policy user="{operator}">
    <allow send_destination="{SERVICE}" send_type="method_call" send_path="{PATH}" send_interface="{SERVICE}" send_member="AuthStatus"/>
  </policy>
</busconfig>
"""
    header = "#pragma once\n" + "inline constexpr bool kBindingsConfigured=true;\n"
    for key, name in [
        ("service_user", "kServiceUser"),
        ("ui_user", "kUiUser"),
        ("operator_user", "kOperatorUser"),
    ]:
        header += (
            "inline constexpr const char* "
            + name
            + "="
            + json.dumps(config[key])
            + ";\n"
        )
    for key, name in [
        ("read_only_verified", "kReadOnlyVerified"),
        ("manual_verified", "kManualVerified"),
    ]:
        header += (
            "inline constexpr bool " + name + "=" + str(config[key]).lower() + ";\n"
        )
    return header, policy


def main():
    source, output, policy = sys.argv[1:]
    policy = Path(policy)
    if source:
        header, xml = render(json.loads(Path(source).read_text()))
        policy.write_text(xml)
    else:
        header = '#pragma once\ninline constexpr bool kBindingsConfigured=false,kReadOnlyVerified=false,kManualVerified=false;\ninline constexpr const char* kServiceUser="",*kUiUser="",*kOperatorUser="";\n'
        if policy.exists():
            if not policy.read_text().startswith("<!-- Generated for ONLY " + SERVICE):
                raise ValueError("refuse_to_remove_unknown_policy")
            policy.unlink()
    Path(output).write_text(header)


if __name__ == "__main__":
    main()
