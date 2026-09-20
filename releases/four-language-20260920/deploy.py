#!/usr/bin/env python3
"""Fail-closed, frontend-only release. No credentials or inspect payloads are saved."""
import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from build_manifest import ALLOWLIST, PREFIX, ROOT, generate

FRONTEND = "deploy-frontend-1"
PUBLIC_URL = "https://westoryvisa.com"
STATIC_ROOT = "/usr/share/nginx/html"
NGINX_PATH = "/etc/nginx/conf.d/default.conf"
RELEASE_ROOT = Path("/opt/westoryvisa-releases")
HASH_COMMAND = ("cd /usr/share/nginx/html && "
                "test -z \"$(find . -type l -print)\" && "
                "find . -type f -exec sha256sum {} + && "
                "sha256sum /etc/nginx/conf.d/default.conf")


class Refusal(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def run(args, cwd=None, timeout=120):
    """Never echo potentially secret subprocess stdout/stderr or command arguments."""
    try:
        environment = None
        if args[:2] == ["docker", "compose"]:
            environment = {**os.environ, "COMPOSE_REMOVE_ORPHANS": "false", "COMPOSE_IGNORE_ORPHANS": "true"}
        result = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout, env=environment)
    except (OSError, subprocess.TimeoutExpired):
        raise Refusal("Command could not complete: " + args[0]) from None
    require(result.returncode == 0, "Command failed: " + args[0])
    return result.stdout.decode()


def inspect(container):
    values = json.loads(run(["docker", "inspect", container]))
    require(len(values) == 1, "Expected one inspected container")
    return values[0]


def inspect_optional(container):
    result = subprocess.run(["docker", "inspect", container], capture_output=True, timeout=30)
    if result.returncode:
        # A subsequent successful docker inventory is required before any mutation.
        return None
    values = json.loads(result.stdout)
    require(len(values) == 1, "Unexpected optional container lookup")
    return values[0]


def all_containers(exclude=()):
    identifiers = run(["docker", "ps", "-aq"]).split()
    if not identifiers:
        return {}
    values = json.loads(run(["docker", "inspect", *identifiers]))
    return {item["Id"]: {"image": item["Image"], "started": item["State"]["StartedAt"],
                          "running": item["State"]["Running"]}
            for item in values if item["Id"] not in exclude}


def runtime(container, oneoff=False):
    """Digest all runtime settings, never persist their environment values."""
    config = copy.deepcopy(container["Config"])
    for key in ("Hostname", "Image", "Labels"):
        config.pop(key, None)
    host = copy.deepcopy(container["HostConfig"])
    if oneoff:
        # Compose run changes stdio, restart policy and its explicitly published port.
        for key in ("AttachStdin", "AttachStdout", "AttachStderr", "OpenStdin", "StdinOnce", "Tty"):
            config.pop(key, None)
        for key in ("PortBindings", "RestartPolicy"):
            host.pop(key, None)
    networks = {name: {key: item.get(key) for key in ("NetworkID", "IPAMConfig", "Links", "DriverOpts")}
                for name, item in container["NetworkSettings"]["Networks"].items()}
    return {"config": fingerprint(config), "host": fingerprint(host),
            "mounts": fingerprint(container.get("Mounts", [])), "networks": fingerprint(networks)}


def validate_frontend(container):
    labels = container["Config"].get("Labels") or {}
    require(container["Name"] == "/" + FRONTEND, "Unexpected frontend name")
    require(container["State"]["Running"], "Frontend is not running")
    require(labels.get("com.docker.compose.service") == "frontend", "Unexpected compose service")
    require(not container.get("Mounts"), "Frontend mounts require manual review; refusing")
    return labels


def compose_identity(container):
    labels = validate_frontend(container)
    project = labels.get("com.docker.compose.project", "")
    directory = Path(labels.get("com.docker.compose.project.working_dir", ""))
    files = labels.get("com.docker.compose.project.config_files", "").split(",")
    require(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project) is not None, "Invalid compose project")
    require(directory.is_absolute() and directory.is_dir(), "Missing compose working directory")
    require(files and all(Path(p).is_absolute() and Path(p).is_file() for p in files),
            "Missing exact compose file list")
    return {"project": project, "cwd": str(directory), "files": files,
            "config_hashes": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files}}


def compose_base_args(identity):
    for path, expected in identity["config_hashes"].items():
        require(Path(path).is_file() and hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected,
                "Compose configuration changed")
    args = ["docker", "compose", "--project-name", identity["project"], "--project-directory", identity["cwd"]]
    for path in identity["files"]:
        args.extend(["-f", path])
    return args


def compose_args(identity, override):
    return compose_base_args(identity) + ["-f", str(override)]


def effective_compose_digest(identity):
    # Includes resolved .env and env_file input without printing or saving values.
    payload = json.loads(run(compose_base_args(identity) + ["config", "--format", "json"], cwd=identity["cwd"]))
    return fingerprint(payload)


def network_aliases(container):
    return {name: sorted(item.get("Aliases") or []) for name, item in container["NetworkSettings"]["Networks"].items()}


def parse_hashes(output):
    static = {}
    nginx = None
    for line in output.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, "Unexpected hash output")
        value, path = match.groups()
        if path == NGINX_PATH:
            require(nginx is None, "Duplicate nginx hash")
            nginx = value
        else:
            require(path.startswith("./"), "Unexpected static hash path")
            name = path[2:]
            require(name not in static and ".." not in Path(name).parts, "Duplicate or unsafe static path")
            static[name] = value
    require(nginx is not None, "Missing nginx hash")
    return static, nginx


def split_metadata(files):
    """AppleDouble files were excluded from source provenance, not from preservation."""
    metadata = {name: value for name, value in files.items()
                if any(part.startswith("._") for part in Path(name).parts)}
    application = {name: value for name, value in files.items() if name not in metadata}
    return application, metadata


def check_hashes(container, expected, nginx, expected_metadata=None):
    actual_files, actual_nginx = parse_hashes(run(["docker", "exec", container, "sh", "-c", HASH_COMMAND]))
    actual_static, actual_metadata = split_metadata(actual_files)
    require(actual_static == expected, "Static application file set or hashes differ")
    if expected_metadata is not None:
        require(actual_metadata == expected_metadata, "Preserved AppleDouble metadata file set or hashes differ")
    require(actual_nginx == nginx, "Actual nginx configuration differs")
    run(["docker", "exec", container, "nginx", "-t"])
    return actual_metadata


def check_clean_base(image, manifest, expected_metadata):
    # Ephemeral, networkless hash process; it does not start nginx or any service.
    output = run(["docker", "run", "--rm", "--network", "none", "--pull", "never",
                  "--entrypoint", "sh", image, "-c", HASH_COMMAND])
    actual_files, nginx = parse_hashes(output)
    actual, metadata = split_metadata(actual_files)
    require(actual == manifest["original_static"] and nginx == manifest["nginx_sha256"],
            "Rollback image differs from running static/nginx baseline")
    require(metadata == expected_metadata, "Rollback image AppleDouble metadata differs from running baseline")


def http(base, path):
    request = urllib.request.Request(base + path, headers={"Host": "westoryvisa.com", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (urllib.error.URLError, TimeoutError):
        raise Refusal("HTTP verification unavailable") from None


def health(base, require_idle=False):
    status, content = http(base, "/api/health")
    require(status == 200, "API health HTTP status is not 200")
    try:
        payload = json.loads(content)
    except ValueError:
        raise Refusal("API health was not JSON") from None
    require(payload.get("ok") is True and payload.get("apiRevision") == 22, "API revision/health changed")
    if require_idle:
        agent = payload.get("agent", {})
        require(agent.get("ready") is True and agent.get("connected") is True,
                "Automation health is not ready; cannot establish safe cutover")
        require(type(agent.get("activeJobs")) is int and agent["activeJobs"] == 0,
                "Active automation jobs or unknown job count; refusing cutover")
    return {"api_revision": 22, "active_jobs": payload.get("agent", {}).get("activeJobs")}


def http_verify(base, expected):
    routes = {"/": "product.html", "/workspace": "workspace.html", "/membership": "membership.html",
              "/extension.html": "extension.html", "/styles.css": "styles.css", "/app.js": "app.js"}
    for route, name in routes.items():
        status, body = http(base, route)
        require(status == 200 and hashlib.sha256(body).hexdigest() == expected[name], "HTTP static route verification failed")
    health(base)
    token = "x" * 48
    status, _ = http(base, "/computer-use/agent-v2-a/view/" + token + "/vnc.html")
    require(status in (401, 403), "Unauthenticated browser view was not rejected")


def loopback(container):
    bindings = container["NetworkSettings"]["Ports"].get("80/tcp")
    require(bindings and len(bindings) == 1, "Expected one frontend HTTP port")
    item = bindings[0]
    require(item["HostIp"] in ("0.0.0.0", "127.0.0.1"), "Unexpected bind address")
    require(str(item["HostPort"]).isdigit(), "Invalid frontend port")
    return "http://127.0.0.1:" + item["HostPort"]


def candidate_verify(container, original, manifest, image, expected_metadata):
    require(container["Image"] == image and container["State"]["Running"], "Candidate image or state differs")
    require(runtime(container, True) == runtime(original, True), "Candidate effective runtime differs")
    require(not container.get("Mounts"), "Candidate unexpectedly mounts data")
    labels = container["Config"].get("Labels") or {}
    require(str(labels.get("com.docker.compose.oneoff", "")).lower() == "true", "Candidate is not one-off")
    require(container["HostConfig"].get("RestartPolicy", {}).get("Name") in ("", "no"), "Candidate restart policy unsafe")
    require(not container["HostConfig"].get("AutoRemove"), "Candidate must remain inspectable")
    for network in container["NetworkSettings"]["Networks"].values():
        require("frontend" not in (network.get("Aliases") or []) and FRONTEND not in (network.get("Aliases") or []),
                "Candidate claims production frontend DNS alias")
    bindings = container["NetworkSettings"]["Ports"].get("80/tcp")
    require(bindings and len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1", "Candidate is not loopback-only")
    check_hashes(container["Id"], manifest["expected_static"], manifest["nginx_sha256"], expected_metadata)
    http_verify(loopback(container), manifest["expected_static"])


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def receipt(state):
    return json.loads((state / "receipt.json").read_text())


def save_receipt(state, value):
    write_json(state / "receipt.json", value)


def override(path, image):
    require(re.fullmatch(r"[a-z0-9][a-z0-9._:/-]+", image) is not None, "Invalid image tag")
    with open(path, "x") as stream:
        stream.write("services:\n  frontend:\n    image: " + image + "\n")


def status(phase, result, **values):
    print(json.dumps({"phase": phase, "status": result, **values}, sort_keys=True), flush=True)


def manifest_verified():
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
    require(manifest == generate(ROOT), "Manifest/source/provenance validation failed")
    return manifest


def ensure_tag_absent(tag):
    result = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
    require(result.returncode != 0, "Release image tag already exists")


def stop_candidate(record):
    if not record.get("candidate_id"):
        value = inspect_optional(record["candidate_name"])
        if value is None:
            return
        require(value["Image"] == record.get("new_image"), "Named candidate is not this release image")
        require(value["Config"].get("Labels", {}).get("com.docker.compose.project") == record["compose"]["project"],
                "Named candidate is not this compose project")
        record["candidate_id"] = value["Id"]
    value = inspect(record["candidate_id"])
    require(value["Name"] == "/" + record["candidate_name"], "Candidate identity changed")
    require(str(value["Config"].get("Labels", {}).get("com.docker.compose.oneoff", "")).lower() == "true",
            "Not an owned one-off candidate")
    if value["State"]["Running"]:
        run(["docker", "stop", "--time", "10", value["Id"]])


def prepare(args, state, manifest):
    require(not state.exists(), "State directory exists; use a new unique directory")
    original = inspect(FRONTEND)
    identity = compose_identity(original)
    require(original["Image"] == manifest["base_image_id"], "Production base image changed")
    metadata = check_hashes(original["Id"], manifest["original_static"], manifest["nginx_sha256"])
    check_clean_base(original["Image"], manifest, metadata)
    status("prepare", "base_and_rollback_verified", static_files=45, preserved_metadata_files=len(metadata))
    local = loopback(original)
    health(local)
    health(PUBLIC_URL)
    short = args.commit[:12]
    unique = state.name
    old_tag = "westoryvisa-frontend:rollback-" + unique
    new_tag = "westoryvisa-frontend:language-" + unique
    ensure_tag_absent(old_tag)
    ensure_tag_absent(new_tag)
    state.mkdir(mode=0o700)
    record = {"schema": 1, "phase": "preparing", "commit": args.commit,
              "manifest_sha256": fingerprint(manifest), "old_id": original["Id"],
              "static_metadata": metadata,
              "old_image": original["Image"], "old_tag": old_tag, "new_tag": new_tag,
              "compose": identity, "runtime": runtime(original), "run_runtime": runtime(original, True),
              "effective_compose_sha256": effective_compose_digest(identity),
              "network_aliases": network_aliases(original),
              "env_sha256": fingerprint(original["Config"].get("Env", [])),
              "other_containers": all_containers([original["Id"]]), "local_url": local,
              "candidate_name": "wvisa-language-check-" + unique, "candidate_id": None}
    save_receipt(state, record)
    try:
        run(["docker", "tag", original["Image"], old_tag])
        run(["docker", "image", "save", "-o", str(state / "rollback-image.tar"), old_tag], timeout=300)
        run(["docker", "cp", original["Id"] + ":" + STATIC_ROOT, str(state / "original-static")])
        run(["docker", "cp", original["Id"] + ":" + NGINX_PATH, str(state / "original-nginx.conf")])
        status("prepare", "rollback_archive_preserved")
        context = state / "build-context"
        context.mkdir()
        for name in ALLOWLIST:
            shutil.copy2(ROOT / PREFIX / name, context / name)
        (context / "Dockerfile").write_text("FROM " + old_tag + "\nCOPY " + json.dumps([*ALLOWLIST, STATIC_ROOT + "/"]) + "\n")
        run(["docker", "build", "--pull=false", "--network=none", "--tag", new_tag, str(context)], timeout=300)
        record["new_image"] = json.loads(run(["docker", "image", "inspect", new_tag]))[0]["Id"]
        status("prepare", "candidate_image_built")
        override(state / "new-image.yml", new_tag)
        override(state / "rollback-image.yml", old_tag)
        save_receipt(state, record)
        command = compose_args(identity, state / "new-image.yml") + [
            "run", "--no-deps", "-d", "-T", "--interactive=false", "--pull", "never",
            "--name", record["candidate_name"], "--publish", "127.0.0.1::80", "frontend"]
        run(command, cwd=identity["cwd"])
        candidate = inspect(record["candidate_name"])
        record["candidate_id"] = candidate["Id"]
        save_receipt(state, record)
        for attempt in range(10):
            try:
                candidate_verify(inspect(candidate["Id"]), original, manifest, record["new_image"], metadata)
                break
            except Refusal:
                if attempt == 9:
                    raise
                time.sleep(1)
        require(inspect(FRONTEND)["Id"] == original["Id"], "Production frontend changed during prepare")
        check_hashes(original["Id"], manifest["original_static"], manifest["nginx_sha256"], metadata)
        require(all_containers([original["Id"], candidate["Id"]]) == record["other_containers"], "Other containers changed")
        require(effective_compose_digest(identity) == record["effective_compose_sha256"], "Effective compose inputs changed")
        stop_candidate(record)
        record["phase"] = "prepared"
        save_receipt(state, record)
        status("prepare", "prepared", commit=args.commit, static_files=48, state_dir=str(state),
               original_preserved=True, candidate_stopped=True)
    except Exception:
        record["phase"] = "prepare_failed"
        save_receipt(state, record)
        stop_candidate(record)
        raise


def validate_receipt(args, state, manifest):
    value = receipt(state)
    require(value.get("commit") == args.commit and value.get("manifest_sha256") == fingerprint(manifest), "Receipt does not match release")
    require(value["old_image"] == manifest["base_image_id"], "Receipt base image differs")
    for filename, tag, image in (("new-image.yml", "new_tag", "new_image"), ("rollback-image.yml", "old_tag", "old_image")):
        require((state / filename).read_text() == "services:\n  frontend:\n    image: " + value[tag] + "\n", "Override changed")
        require(json.loads(run(["docker", "image", "inspect", value[tag]]))[0]["Id"] == value[image], "Release image tag changed")
    return value


def verify_live(record, manifest, expected_image, expected_static):
    current = inspect(FRONTEND)
    validate_frontend(current)
    require(current["Image"] == expected_image, "Active image differs")
    require(runtime(current) == record["runtime"], "Active frontend runtime differs")
    require(network_aliases(current) == record["network_aliases"], "Active frontend DNS aliases differ")
    require(all_containers([current["Id"], record["candidate_id"]]) == record["other_containers"], "Other containers changed")
    check_hashes(current["Id"], expected_static, manifest["nginx_sha256"], record["static_metadata"])
    http_verify(loopback(current), expected_static)
    http_verify(PUBLIC_URL, expected_static)
    return current


def compose_up(record, state, filename):
    require(effective_compose_digest(record["compose"]) == record["effective_compose_sha256"],
            "Effective compose configuration/environment changed")
    args = compose_args(record["compose"], state / filename)
    run(args + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "frontend"],
        cwd=record["compose"]["cwd"], timeout=180)


def verify_with_retry(record, manifest, image, files):
    for attempt in range(12):
        try:
            return verify_live(record, manifest, image, files)
        except Refusal:
            if attempt == 11:
                raise
            time.sleep(1)


def rollback(record, state, manifest, automatic=False):
    # Called automatically only after this operation attempted its frontend cutover.
    current = inspect_optional(FRONTEND)
    if current is not None:
        require(current["Image"] in (record["old_image"], record["new_image"]), "Unrelated frontend image now active; refusing rollback")
    else:
        identifiers = run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + record["compose"]["project"],
                           "--filter", "label=com.docker.compose.service=frontend"]).split()
        for identifier in identifiers:
            item = inspect(identifier)
            require(item["Id"] == record.get("candidate_id") and
                    str(item["Config"].get("Labels", {}).get("com.docker.compose.oneoff", "")).lower() == "true",
                    "Unexpected frontend container exists during recovery")
    excluded = [record["candidate_id"]] + ([current["Id"]] if current else [])
    require(all_containers(excluded) == record["other_containers"], "Other containers changed; manual review required")
    compose_up(record, state, "rollback-image.yml")
    restored = verify_with_retry(record, manifest, record["old_image"], manifest["original_static"])
    record["phase"] = "rolled_back"
    record["restored_id"] = restored["Id"]
    save_receipt(state, record)
    status("rollback", "rolled_back", automatic=automatic, frontend_only=True, image=record["old_image"])


def activate(args, state, manifest):
    record = validate_receipt(args, state, manifest)
    require(record["phase"] == "prepared", "Release is not prepared")
    current = inspect(FRONTEND)
    require(current["Id"] == record["old_id"], "Frontend changed since prepare")
    verify_live(record, manifest, record["old_image"], manifest["original_static"])
    health(record["local_url"], require_idle=True)
    health(PUBLIC_URL, require_idle=True)
    record["phase"] = "activating"
    save_receipt(state, record)
    try:
        # Recheck identity immediately before the sole production mutation.
        require(inspect(FRONTEND)["Id"] == record["old_id"], "Concurrent frontend change")
        compose_up(record, state, "new-image.yml")
        new = verify_with_retry(record, manifest, record["new_image"], manifest["expected_static"])
        record["phase"] = "active"
        record["active_id"] = new["Id"]
        save_receipt(state, record)
        status("activate", "active", commit=args.commit, static_files=48, frontend_only=True,
               other_containers_unchanged=True, rollback_state=str(state))
    except Exception:
        record["phase"] = "activation_failed"
        save_receipt(state, record)
        status("activate", "failed_rolling_back")
        try:
            rollback(record, state, manifest, automatic=True)
        except Exception:
            status("rollback", "failed_manual_action_required", state_dir=str(state))
        raise


@contextlib.contextmanager
def global_lock():
    RELEASE_ROOT.mkdir(mode=0o700, exist_ok=True)
    require(not RELEASE_ROOT.is_symlink(), "Release root must not be a symlink")
    descriptor = os.open(str(RELEASE_ROOT / ".frontend-release.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refusal("Another frontend deployment is running") from None
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("verify-source", "prepare", "activate", "rollback"))
    parser.add_argument("--commit", required=True)
    parser.add_argument("--state-dir")
    args = parser.parse_args()
    require(re.fullmatch(r"[0-9a-f]{40}", args.commit) is not None, "Expected immutable 40-character commit SHA")
    manifest = manifest_verified()
    if args.phase == "verify-source":
        status(args.phase, "verified", original_source_files=366, replacements=13, additions=3)
        return
    require(os.geteuid() == 0, "Server deployment requires root")
    require(args.state_dir is not None, "Explicit unique --state-dir is required")
    state = Path(args.state_dir)
    require(state.is_absolute() and state.parent == RELEASE_ROOT and
            re.fullmatch(args.commit[:12] + r"-[a-z0-9][a-z0-9-]{0,40}", state.name) is not None,
            "State must be /opt/westoryvisa-releases/<commit12>-<unique-suffix>")
    require(not state.is_symlink() and state.resolve() == state and state not in ROOT.parents and state != ROOT,
            "Unsafe state path")
    os.umask(0o077)
    with global_lock():
        if args.phase == "prepare":
            prepare(args, state, manifest)
        elif args.phase == "activate":
            activate(args, state, manifest)
        else:
            record = validate_receipt(args, state, manifest)
            require(record["phase"] in ("active", "activation_failed", "activating"), "Release is not eligible for rollback")
            rollback(record, state, manifest)


if __name__ == "__main__":
    try:
        main()
    except Refusal as error:
        status("error", "refused", reason=str(error))
        sys.exit(1)
    except Exception as error:
        # Do not print subprocess arguments, environment, HTTP bodies or tracebacks.
        status("error", "unexpected_failure", error_type=type(error).__name__)
        sys.exit(1)
