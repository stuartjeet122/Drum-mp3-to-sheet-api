"""Rebuild and update the existing drum-transcription Portainer standalone stack."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler, Request

import yaml


STACK_NAME = "drum-transcription"
VERIFY_FIX = (
    "import inspect; from engine.models.base import pick_events; "
    "assert 'np.clip' in inspect.getsource(pick_events), 'Image contains old velocity code'; "
    "print('Velocity fix verified')"
)


def command(arguments: list[str], *, capture: bool = True, timeout: int = 60) -> str:
    result = subprocess.run(arguments, check=True, text=True, capture_output=capture, timeout=timeout)
    return result.stdout.strip() if capture else ""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Portainer redirected the request; use its direct API URL.")


class Portainer:
    def __init__(self, url: str, token: str, ca_file: str | None = None) -> None:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Use a direct HTTP(S) Portainer URL without embedded credentials.")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Unencrypted Portainer access is allowed only over loopback.")
        self.url = url.rstrip("/")
        self.token = token
        self.opener = build_opener(
            NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)),
        )

    def request(self, method: str, path: str, body: dict | None = None):
        request = Request(
            self.url + "/api" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-API-Key": self.token, "Content-Type": "application/json"},
            method=method,
        )
        try:
            with self.opener.open(request, timeout=3900 if method == "PUT" else 30) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(
                f"Portainer {method} {path.split('?')[0]} returned HTTP {error.code}; "
                "check token permissions and Portainer logs."
            ) from None


def updated_stack(content: str, image: str) -> str:
    configuration = yaml.safe_load(content)
    if not isinstance(configuration, dict):
        raise ValueError("Portainer stack content must be a Compose mapping.")
    services = configuration.get("services", {})
    if set(services) != {"api", "worker", "redis"}:
        raise ValueError("Expected exactly api, worker and redis; refusing to alter another stack.")
    if not services["api"].get("healthcheck"):
        raise ValueError("The API must have a health check before automatic deployment.")
    for service in (services["api"], services["worker"], configuration.get("x-application", {})):
        service["image"] = image
        service["pull_policy"] = "never"
        service.pop("build", None)
    return yaml.safe_dump(configuration, sort_keys=False)


def update_payload(content: str, environment: list) -> dict:
    return {
        "StackFileContent": content, "Env": environment,
        "Prune": False, "PullImage": False, "RepullImageAndRedeploy": False,
    }


def stack_unchanged(client: Portainer, stack: dict, content: str) -> None:
    current = client.request("GET", f"/stacks/{stack['Id']}")
    current_file = client.request("GET", f"/stacks/{stack['Id']}/file")
    if (current_file["StackFileContent"] != content
            or current.get("Env", []) != stack.get("Env", [])
            or current.get("EndpointId") != stack["EndpointId"]
            or current.get("GitConfig") or current.get("AutoUpdate")):
        raise RuntimeError("Stack changed during the build; no deployment was requested. Run again.")


def wait_for_containers(image_id: str, timeout: int) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = {}
        for service in ("api", "worker"):
            identifiers = command([
                "docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={STACK_NAME}",
                "--filter", f"label=com.docker.compose.service={service}",
            ]).split()
            if len(identifiers) != 1:
                continue
            container = json.loads(command(["docker", "inspect", identifiers[0]]))[0]
            state = container["State"]
            if (container["Image"] == image_id and state.get("Running")
                    and (service != "api" or state.get("Health", {}).get("Status") == "healthy")):
                found[service] = identifiers[0]
        if len(found) == 2:
            command(["docker", "exec", found["worker"], "python", "-c", VERIFY_FIX])
            return found
        time.sleep(2)
    raise RuntimeError(
        "Timed out waiting for the new API/worker. Check Portainer deployment/container logs. "
        "Do not remove volumes; old images and the configuration backup were retained."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="Trusted public HTTPS Git repository URL.")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--portainer-url", default="http://127.0.0.1:9000")
    parser.add_argument("--ca-file", help="Trusted CA certificate for a custom HTTPS Portainer URL.")
    parser.add_argument("--timeout", type=int, default=900, help="Deployment readiness deadline in seconds.")
    arguments = parser.parse_args()
    repository = urlsplit(arguments.repository)
    if (repository.scheme != "https" or not repository.hostname or repository.username
            or repository.password or repository.query or repository.fragment):
        parser.error("--repository must be a trusted public HTTPS Git URL without credentials or fragments.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", arguments.branch) or ".." in arguments.branch:
        parser.error("Invalid Git branch.")
    if arguments.timeout < 1:
        parser.error("--timeout must be positive.")

    try:
        token = getpass.getpass("Portainer API access token (hidden): ").strip()
        if not token:
            raise ValueError("An API access token is required.")
        client = Portainer(arguments.portainer_url, token, arguments.ca_file)
        matches = [stack for stack in client.request("GET", "/stacks") if stack["Name"] == STACK_NAME]
        if len(matches) != 1:
            raise ValueError("Expected exactly one Portainer stack named drum-transcription.")
        stack = client.request("GET", f"/stacks/{matches[0]['Id']}")
        if stack["Type"] != 2 or stack.get("GitConfig") or stack.get("AutoUpdate"):
            raise ValueError("Only the existing web-editor Docker Standalone stack is supported; disable auto-update first.")
        endpoint = stack["EndpointId"]
        remote_docker = client.request("GET", f"/endpoints/{endpoint}/docker/info")
        if command(["docker", "info", "--format", "{{.ID}}"]) != remote_docker["ID"]:
            raise ValueError("Run this script on the Docker host/context managed by this Portainer stack.")
        content = client.request("GET", f"/stacks/{stack['Id']}/file")["StackFileContent"]
        updated_stack(content, "drum-transcription:preflight")
        revision_line = command([
            "git", "ls-remote", "--exit-code", arguments.repository, f"refs/heads/{arguments.branch}",
        ])
        revision = revision_line.split()[0]
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Git did not return a full commit ID.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        image = f"drum-transcription:{revision[:12]}-{stamp}"
        print(f"Building trusted revision {revision} as {image}. Existing services remain running.", flush=True)
        command([
            "docker", "build", "--platform", "linux/amd64", "--label",
            f"org.opencontainers.image.revision={revision}", "-t", image,
            f"{arguments.repository}#{revision}",
        ], capture=False, timeout=7200)
        image_id = command(["docker", "image", "inspect", "--format", "{{.Id}}", image])
        command([
            "docker", "run", "--rm", "--pull=never", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--entrypoint", "python", image, "-c", VERIFY_FIX,
        ])
        replacement = updated_stack(content, image)
        stack_unchanged(client, stack, content)
        backup_dir = Path(tempfile.mkdtemp(prefix="drum-portainer-", dir="/var/tmp"))
        backup = backup_dir / "stack.json"
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(update_payload(content, stack.get("Env", [])), handle, indent=2)
        print(f"Private stack/environment backup: {backup}", flush=True)
        print("Redeploying through Portainer; API downtime is expected. No volumes or images will be deleted.", flush=True)
        client.request(
            "PUT", f"/stacks/{stack['Id']}?endpointId={endpoint}",
            update_payload(replacement, stack.get("Env", [])),
        )
        containers = wait_for_containers(image_id, arguments.timeout)
        print(f"Updated image verified in API {containers['api'][:12]} and worker {containers['worker'][:12]}.")
        print("Retry the failed job or run the smoke test. Real inference has not been tested by this updater.")
        return 0
    except (OSError, ValueError, RuntimeError, yaml.YAMLError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        if isinstance(error, subprocess.CalledProcessError):
            print("Update failed: a Git/Docker command failed. Check its output and Portainer logs.", file=sys.stderr)
        else:
            print(f"Update failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())