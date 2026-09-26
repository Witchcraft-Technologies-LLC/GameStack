"""Strict, versioned GamePack loading; no executable YAML or templating."""
import ipaddress
from pathlib import Path, PurePosixPath
import re

import yaml


class GameStackError(Exception):
    """An expected error safe to display to the user."""


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise GameStackError("YAML keys must be unique strings. Check your configuration.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


CONFIG_LIMIT = 256_000


def parse_yaml(payload: bytes) -> dict:
    if len(payload) > CONFIG_LIMIT:
        raise GameStackError("Configuration is too large. Keep it below 256 KB.")
    try:
        value = yaml.load(payload.decode("utf-8"), Loader=UniqueLoader)
    except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
        # Parser errors can contain entire secret-bearing source lines.
        raise GameStackError("Cannot read configuration. Check the file, permissions, and YAML syntax.") from exc
    if not isinstance(value, dict):
        raise GameStackError("Configuration must be a YAML mapping. Check the documented format.")
    return value


def read_yaml(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            payload = stream.read(CONFIG_LIMIT + 1)
    except OSError as exc:
        raise GameStackError("Cannot read configuration. Check the file, permissions, and YAML syntax.") from exc
    return parse_yaml(payload)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GameStackError(message + " Check the GamePack format in docs/gamepacks.md.")


def fields(value: dict, required: set[str], optional: set[str] = frozenset()) -> None:
    require(isinstance(value, dict), "Expected a mapping.")
    require(required <= value.keys() and value.keys() <= required | optional,
            "Missing or unknown configuration fields.")


def name(value: str) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,39}", value) is not None,
            "Names must start with a lowercase letter and contain up to 40 lowercase letters, digits, or hyphens.")
    return value


def string(value: object) -> bool:
    return isinstance(value, str) and bool(value) and all(ord(c) >= 32 for c in value)


def validate(pack: dict) -> dict:
    require(isinstance(pack, dict), "GamePack must be a mapping.")
    version = pack.get("schema_version")
    require(type(version) is int and version in (1, 2), "Unsupported schema version.")
    fields(pack, {"schema_version", "id", "version", "name", "image", "data_path", "ports", "environment", "healthcheck", "stop_timeout"} | ({"startup_timeout"} if version == 2 else set()), {"user"})
    if version == 2:
        require(type(pack["startup_timeout"]) is int and 30 <= pack["startup_timeout"] <= 1800, "Startup timeout must be 30–1800 seconds.")
    name(pack["id"])
    require(string(pack["name"]) and string(pack["version"]), "Name and version must be nonempty strings.")
    require(isinstance(pack["image"], str) and re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}", pack["image"]) is not None,
            "Image must be pinned to a sha256 digest.")
    target = pack["data_path"]
    require(string(target), "Storage path must be a string.")
    p = PurePosixPath(target)
    require(p.is_absolute() and len(p.parts) > 1 and ".." not in p.parts and str(p) == target and "$" not in target,
            "Storage must use a normalized absolute container path below root.")
    require(target != "/var/run/docker.sock", "The container cannot mount the Docker socket.")
    require(type(pack["stop_timeout"]) is int and 10 <= pack["stop_timeout"] <= 600, "Shutdown timeout must be 10–600 seconds.")
    if "user" in pack and not (version == 2 and pack["user"] == "installing-user"):
        require(isinstance(pack["user"], str) and re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", pack["user"]) is not None,
                "User must be a non-root numeric UID:GID string.")
    require(isinstance(pack["ports"], list), "Ports must be a list.")
    seen = set()
    for port in pack["ports"]:
        fields(port, {"host", "container", "protocol", "purpose"})
        require(all(type(port[k]) is int and 1 <= port[k] <= 65535 for k in ("host", "container")), "Port numbers must be 1–65535.")
        require(port["protocol"] in ("tcp", "udp") and string(port["purpose"]), "Ports need a protocol and purpose.")
        binding = (port["host"], port["protocol"])
        require(binding not in seen, "Duplicate host port.")
        seen.add(binding)
    require(isinstance(pack["environment"], dict), "Environment must be a mapping.")
    for key, setting in pack["environment"].items():
        require(re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) is not None, "Invalid environment variable name.")
        require(isinstance(setting, dict), "Settings must be mappings.")
        if version == 2 and "value" in setting:
            fields(setting, {"value"})
            require(string(setting["value"]), "Fixed values must be nonempty strings.")
            continue
        fields(setting, {"prompt", "secret"}, {"default"} | ({"constraints", "agreement"} if version == 2 else set()))
        if "constraints" in setting:
            rules = setting["constraints"]
            fields(rules, {"characters", "min_length", "max_length"}, {"separator"})
            require(string(rules["characters"]), "Allowed characters must be a nonempty string.")
            require(type(rules["min_length"]) is int and type(rules["max_length"]) is int and 1 <= rules["min_length"] <= rules["max_length"] <= 1024, "Invalid setting length constraints.")
            if "separator" in rules:
                require(isinstance(rules["separator"], str) and len(rules["separator"]) == 1 and rules["separator"] not in rules["characters"], "Invalid list separator.")
        if "agreement" in setting:
            require(string(setting["agreement"]) and setting["agreement"].startswith("https://") and "default" not in setting and not setting["secret"], "Agreements require an HTTPS URL, no default, and a visible prompt.")
        require(string(setting["prompt"]) and type(setting["secret"]) is bool, "Settings require a prompt and boolean secret flag.")
        if "default" in setting:
            require(string(setting["default"]) and not setting["secret"], "Defaults must be nonempty strings and cannot contain secrets.")
            validate_value(key, setting, setting["default"])
    health = pack["healthcheck"]
    require(isinstance(health, list) and bool(health) and all(string(v) for v in health), "Healthcheck must be a nonempty argument list.")
    return pack


def load_pack(path: Path) -> dict:
    return validate(read_yaml(path))


def validate_value(key: str, setting: dict, value: object) -> str:
    if not string(value):
        raise GameStackError(f"{key} must be a nonempty single-line string. Quote values in your settings file.")
    if "value" in setting and value != setting["value"]:
        raise GameStackError(f"{key} is fixed by the GamePack and cannot be overridden.")
    if "agreement" in setting and value != "TRUE":
        raise GameStackError(f"{key} requires explicit acceptance. Read {setting['agreement']} and supply the quoted value TRUE only if you agree.")
    rules = setting.get("constraints")
    if rules:
        items = value.split(rules["separator"]) if "separator" in rules else [value]
        if len(value) > 4096 or any(not rules["min_length"] <= len(item) <= rules["max_length"] or any(c not in rules["characters"] for c in item) for item in items):
            raise GameStackError(f"{key} has an invalid value. {setting['prompt']}; use {rules['min_length']}–{rules['max_length']} allowed characters per entry, with no spaces around separators.")
    return value


def validate_values(pack: dict, values: dict) -> None:
    require(values.keys() == pack["environment"].keys(), "Every configuration setting is required.")
    for key, setting in pack["environment"].items():
        validate_value(key, setting, values[key])


def bind_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
        if "%" in value or address.is_multicast or address == ipaddress.ip_address("255.255.255.255"):
            raise ValueError()
        return str(address)
    except ValueError as exc:
        raise GameStackError("Bind address must be a numeric unicast IP address or 0.0.0.0 / :: for all interfaces.") from exc
