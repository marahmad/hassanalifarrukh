#!/usr/bin/env python3
"""Automated local setup & verification for Oracle OCI Cloud MCP Server.

Configures `oracle.oci-cloud-mcp-server` (via `uvx`) for Antigravity IDE / CLI
(and optionally Claude Desktop or Cursor), verifies the live MCP stdio
handshake, and validates/assists with `~/.oci/config`.
"""

from __future__ import annotations

import argparse
import configparser
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_SERVER_NAME = "oracle-oci-cloud"
DEFAULT_MCP_PACKAGE = "oracle.oci-cloud-mcp-server@latest"
EXPECTED_TOOLS = {
    "list_oci_clients",
    "find_oci_api",
    "describe_oci_operation",
    "invoke_oci_api",
    "list_client_operations",
}


def info(msg: str) -> None:
    print(f"[info] {msg}")


def ok(msg: str) -> None:
    print(f"[ok]   {msg}")


def warn(msg: str) -> None:
    print(f"[warn] {msg}")


def fail(msg: str) -> None:
    print(f"[err]  {msg}", file=sys.stderr)


def find_executable(name: str) -> str | None:
    """Locate an executable on PATH or in standard uv install directories."""
    hit = shutil.which(name)
    if hit:
        return hit

    home = Path.home()
    candidates: list[Path] = []
    if os.name == "nt":
        exe = f"{name}.exe"
        local_app_data = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        candidates.extend(
            [
                home / ".local" / "bin" / exe,
                home / ".cargo" / "bin" / exe,
                local_app_data / "Microsoft" / "WinGet" / "Links" / exe,
                local_app_data / "uv" / exe,
            ]
        )
    else:
        candidates.extend(
            [
                home / ".local" / "bin" / name,
                home / ".cargo" / "bin" / name,
                Path("/opt/pipx/bin") / name,
                Path("/opt/homebrew/bin") / name,
                Path("/usr/local/bin") / name,
            ]
        )

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def ensure_uv(verify_only: bool) -> tuple[str, str]:
    """Ensure `uv` and `uvx` are installed and return their paths."""
    uv_bin = find_executable("uv")
    uvx_bin = find_executable("uvx")
    if uv_bin and uvx_bin:
        ok(f"Found uv: {uv_bin}")
        return uv_bin, uvx_bin

    if verify_only:
        raise RuntimeError("`uv` / `uvx` not found. Run without --verify-only to install it.")

    info("`uv` not found — attempting installation...")
    if os.name == "nt":
        winget = find_executable("winget")
        if not winget:
            raise RuntimeError(
                "winget not found. Install uv manually from https://docs.astral.sh/uv/ "
                "and re-run this script."
            )
        subprocess.run(
            [
                winget,
                "install",
                "--id=astral-sh.uv",
                "-e",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ],
            check=True,
        )
    else:
        curl = find_executable("curl")
        if not curl:
            raise RuntimeError(
                "curl not found. Install uv manually from https://docs.astral.sh/uv/ "
                "and re-run this script."
            )
        subprocess.run(
            ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
            check=True,
        )

    uv_bin = find_executable("uv")
    uvx_bin = find_executable("uvx")
    if not uv_bin or not uvx_bin:
        raise RuntimeError(
            "uv was installed, but the current shell cannot see it yet. "
            "Open a new terminal and re-run this script."
        )
    ok(f"Installed uv: {uv_bin}")
    return uv_bin, uvx_bin


def ensure_windows_tmp_dir(verify_only: bool) -> None:
    """Create C:\\tmp on Windows because oracle.oci-cloud-mcp-server 2.3.x writes /tmp/audit.log."""
    if os.name != "nt":
        return
    tmp_dir = Path("C:/tmp")
    if tmp_dir.is_dir():
        ok("Windows C:\\tmp directory exists (required for OCI MCP audit.log)")
        return
    if verify_only:
        warn("Windows C:\\tmp does not exist; oracle.oci-cloud-mcp-server 2.3.x requires it.")
        return
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ok("Created C:\\tmp (required by oracle.oci-cloud-mcp-server 2.3.x audit logger)")


def ensure_python_313(uv_bin: str, verify_only: bool) -> None:
    """Ensure Python 3.13 is available to uv."""
    if verify_only:
        res = subprocess.run([uv_bin, "python", "find", "3.13"], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError("Python 3.13 not installed via uv (`uv python install 3.13`).")
        ok(f"Python 3.13 available: {res.stdout.strip()}")
        return

    info("Ensuring Python 3.13 is installed via uv...")
    subprocess.run([uv_bin, "python", "install", "3.13"], check=True)
    ok("Python 3.13 ready")


def verify_mcp_stdio(uvx_bin: str, profile: str, region: str | None) -> None:
    """Pre-warm oracle.oci-cloud-mcp-server and verify MCP initialize + tools/list."""
    info("Verifying oracle.oci-cloud-mcp-server MCP stdio handshake...")
    env = os.environ.copy()
    env.update(
        {
            "OCI_CONFIG_PROFILE": profile,
            "OCI_MCP_AUTH_TYPE": "auto",
            "FASTMCP_LOG_LEVEL": "ERROR",
        }
    )
    if region:
        env["OCI_REGION"] = region

    uvx_dir = str(Path(uvx_bin).parent)
    if uvx_dir and uvx_dir not in env.get("PATH", ""):
        env["PATH"] = f"{uvx_dir}{os.pathsep}{env.get('PATH', '')}"

    proc = subprocess.Popen(
        [uvx_bin, "--python", "3.13", DEFAULT_MCP_PACKAGE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

    try:
        def send(payload: dict) -> None:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "setup-oracle-mcp", "version": "1.0"},
                },
            }
        )
        init_line = proc.stdout.readline()
        if not init_line:
            stderr_tail = proc.stderr.read()
            raise RuntimeError(f"MCP server closed before initialize response.\n{stderr_tail}")
        init_resp = json.loads(init_line)
        server_info = init_resp.get("result", {}).get("serverInfo", {})

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_line = proc.stdout.readline()
        if not tools_line:
            stderr_tail = proc.stderr.read()
            raise RuntimeError(f"MCP server closed before tools/list response.\n{stderr_tail}")
        tools_resp = json.loads(tools_line)
        tools = {t["name"] for t in tools_resp.get("result", {}).get("tools", [])}

        proc.stdin.close()
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()

    missing = EXPECTED_TOOLS - tools
    if missing:
        raise RuntimeError(f"MCP server responded, but expected tools were missing: {sorted(missing)}")

    ok(
        f"MCP handshake verified ({server_info.get('name', 'oracle.oci-cloud-mcp-server')} "
        f"v{server_info.get('version', '?')}, {len(tools)} tools)"
    )


def resolve_client_targets(client: str, custom_target: str | None) -> list[Path]:
    """Return MCP JSON config file paths to update."""
    if custom_target:
        return [Path(custom_target).expanduser()]

    home = Path.home()
    targets: list[Path] = []

    if client in ("antigravity", "all"):
        targets.append(home / ".gemini" / "config" / "mcp_config.json")
        legacy = home / ".gemini" / "antigravity" / "mcp_config.json"
        if legacy.exists() or legacy.parent.is_dir():
            targets.append(legacy)

    if client in ("claude", "all"):
        if os.name == "nt":
            appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
            targets.append(appdata / "Claude" / "claude_desktop_config.json")
        elif sys.platform == "darwin":
            targets.append(
                home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
            )
        else:
            targets.append(home / ".config" / "Claude" / "claude_desktop_config.json")

    if client in ("cursor", "all"):
        targets.append(home / ".cursor" / "mcp.json")

    # Preserve order while deduplicating
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in targets:
        resolved = path.resolve() if path.exists() else path
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def build_server_entry(uvx_bin: str, profile: str, region: str | None, oci_config_path: Path) -> dict:
    """Build the mcpServers entry using absolute uvx path so GUI apps don't fail on PATH."""
    command = uvx_bin.replace("\\", "/") if os.name == "nt" else uvx_bin
    env: dict[str, str] = {
        "OCI_CONFIG_PROFILE": profile,
        "OCI_MCP_AUTH_TYPE": "auto",
        "FASTMCP_LOG_LEVEL": "ERROR",
    }
    if oci_config_path != (Path.home() / ".oci" / "config"):
        env["OCI_CONFIG_FILE"] = str(oci_config_path).replace("\\", "/")
    if region:
        env["OCI_REGION"] = region

    return {
        "command": command,
        "args": ["--python", "3.13", DEFAULT_MCP_PACKAGE],
        "env": env,
    }


def merge_mcp_config(target: Path, server_entry: dict, verify_only: bool) -> bool:
    """Merge oracle-oci-cloud into an MCP client JSON config safely."""
    data: dict = {}
    if target.exists():
        raw = target.read_text(encoding="utf-8").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                fail(f"Skipping {target}: invalid JSON ({exc})")
                return False
            if not isinstance(parsed, dict):
                fail(f"Skipping {target}: root JSON value must be an object.")
                return False
            data = parsed

    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
        data["mcpServers"] = servers
    elif not isinstance(servers, dict):
        fail(f"Skipping {target}: `mcpServers` is not a JSON object.")
        return False

    existing = servers.get(DEFAULT_SERVER_NAME)
    if existing == server_entry:
        ok(f"Already up to date: {target}")
        return True

    if verify_only:
        warn(f"Config missing or differs at {target} (run without --verify-only to write)")
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = target.with_name(f"{target.name}.bak.{stamp}")
        shutil.copy2(target, backup)
        info(f"Backed up existing config to {backup}")

    servers[DEFAULT_SERVER_NAME] = server_entry
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    ok(f"Configured `{DEFAULT_SERVER_NAME}` in {target}")
    return True


def validate_oci_config(oci_config: Path, profile: str) -> tuple[bool, str]:
    """Validate ~/.oci/config structure and private key file existence."""
    if not oci_config.is_file():
        return False, f"File not found: {oci_config}"

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(oci_config, encoding="utf-8")
    except configparser.Error as exc:
        return False, f"Failed to parse {oci_config}: {exc}"

    if profile == "DEFAULT":
        section = parser.defaults()
        if not section:
            return False, f"No [DEFAULT] section found in {oci_config}"
    else:
        if not parser.has_section(profile):
            return False, f"Profile [{profile}] not found in {oci_config}"
        section = dict(parser.items(profile))

    if "security_token_file" in section:
        token_path = Path(section["security_token_file"]).expanduser()
        if not token_path.is_file():
            return False, f"security_token_file missing: {token_path}"
        return True, "Valid session-token profile"

    required = ("user", "fingerprint", "tenancy", "region", "key_file")
    missing = [k for k in required if not section.get(k, "").strip()]
    if missing:
        return False, f"Missing keys in [{profile}]: {', '.join(missing)}"

    key_path = Path(section["key_file"].strip()).expanduser()
    if not key_path.is_file():
        return False, f"Private key file does not exist: {key_path}"

    return True, f"Valid API-key profile [{profile}] (region={section['region'].strip()})"


def interactive_oci_setup(oci_config: Path, profile: str) -> bool:
    """Guide the user to create ~/.oci/config cleanly on their local machine."""
    print()
    print("=== OCI API Credentials Setup ===")
    print(f"No valid OCI profile [{profile}] found at: {oci_config}")
    print("To get your OCI API credentials:")
    print("  1. Log in to Oracle Cloud Console -> Profile (top right) -> My profile")
    print("  2. Open Tokens and keys -> API keys -> Add API key -> Generate API key pair")
    print("  3. Download the Private Key (.pem) to your PC and click Add")
    print("  4. Copy the shown 'Configuration File Preview' snippet")
    print()

    choice = input("Configure ~/.oci/config now? [Y/n]: ").strip().lower()
    if choice in ("n", "no"):
        info("Skipped ~/.oci/config creation. You can re-run this script anytime.")
        return False

    print()
    print("Paste the OCI Configuration File Preview below, then enter a blank line:")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line)

    snippet = "\n".join(lines).strip()
    if not snippet:
        warn("No config pasted; skipping ~/.oci/config creation.")
        return False

    if not snippet.lstrip().startswith("["):
        snippet = f"[{profile}]\n{snippet}"

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(snippet)
    except configparser.Error as exc:
        fail(f"Could not parse pasted INI snippet: {exc}")
        return False

    if profile != "DEFAULT" and parser.has_section(profile):
        values = dict(parser.items(profile))
    elif parser.defaults():
        values = dict(parser.defaults())
    elif parser.sections():
        values = dict(parser.items(parser.sections()[0]))
    else:
        fail("No configuration keys found in pasted snippet.")
        return False

    key_file_raw = values.get("key_file", "").strip()
    if not key_file_raw or "<" in key_file_raw or not Path(key_file_raw).expanduser().is_file():
        default_pem = str((oci_config.parent / "oci_api_key.pem")).replace("\\", "/")
        entered = input(f"Path to downloaded OCI private key (.pem) [{default_pem}]: ").strip()
        key_file_raw = (entered or default_pem).strip('"').strip("'")

    key_file_norm = key_file_raw.replace("\\", "/") if os.name == "nt" else key_file_raw
    if not Path(key_file_norm).expanduser().is_file():
        warn(f"Key file not found yet at `{key_file_norm}` — saving config anyway so you can place the .pem there.")

    values["key_file"] = key_file_norm

    out_parser = configparser.ConfigParser(interpolation=None)
    if oci_config.is_file():
        try:
            out_parser.read(oci_config, encoding="utf-8")
        except configparser.Error:
            pass

    if profile == "DEFAULT":
        for k, v in values.items():
            out_parser["DEFAULT"][k] = v
    else:
        if not out_parser.has_section(profile):
            out_parser.add_section(profile)
        for k, v in values.items():
            out_parser.set(profile, k, v)

    oci_config.parent.mkdir(parents=True, exist_ok=True)
    with oci_config.open("w", encoding="utf-8") as fh:
        out_parser.write(fh)

    if os.name != "nt":
        os.chmod(oci_config, 0o600)

    ok(f"Saved OCI config to {oci_config}")
    return True


def verify_oci_api_auth(uvx_bin: str, oci_config: Path, profile: str, region: str | None) -> bool:
    """Run a read-only OCI SDK IdentityClient.list_regions() check."""
    info("Testing live OCI SDK authentication (read-only list_regions)...")
    code = (
        "import os, oci; "
        "cfg = oci.config.from_file("
        "file_location=os.environ['SETUP_OCI_CONFIG'], "
        "profile_name=os.environ['SETUP_OCI_PROFILE']); "
        "region = os.environ.get('SETUP_OCI_REGION'); "
        "cfg['region'] = region if region else cfg['region']; "
        "client = oci.identity.IdentityClient(cfg); "
        "regions = client.list_regions().data; "
        "print(f'Authenticated to OCI ({len(regions)} regions available, active={cfg[\"region\"]})')"
    )
    env = os.environ.copy()
    env["SETUP_OCI_CONFIG"] = str(oci_config)
    env["SETUP_OCI_PROFILE"] = profile
    if region:
        env["SETUP_OCI_REGION"] = region

    res = subprocess.run(
        [uvx_bin, "--python", "3.13", "--from", DEFAULT_MCP_PACKAGE, "python", "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if res.returncode != 0:
        warn(f"OCI API check did not succeed yet:\n{res.stderr.strip() or res.stdout.strip()}")
        return False

    for line in res.stdout.strip().splitlines():
        if line.startswith("Authenticated to OCI"):
            ok(line)
            return True
    ok("OCI API authentication verified.")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set up and verify Oracle OCI Cloud MCP Server for Antigravity IDE / CLI."
    )
    parser.add_argument(
        "--client",
        choices=("antigravity", "claude", "cursor", "all"),
        default="antigravity",
        help="Target MCP client configuration to update (default: antigravity).",
    )
    parser.add_argument(
        "--target-config",
        help="Override target mcp_config.json path (skips default client paths).",
    )
    parser.add_argument(
        "--oci-config",
        default=str(Path.home() / ".oci" / "config"),
        help="Path to OCI config file (default: ~/.oci/config).",
    )
    parser.add_argument(
        "--profile",
        default="DEFAULT",
        help="OCI config profile name (default: DEFAULT).",
    )
    parser.add_argument(
        "--region",
        help="Optional OCI region override (e.g. me-dubai-1, us-ashburn-1).",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt for ~/.oci/config if credentials are missing.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify uv, MCP handshake, config files, and OCI auth without making changes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    oci_config_path = Path(args.oci_config).expanduser()

    try:
        uv_bin, uvx_bin = ensure_uv(args.verify_only)
        ensure_windows_tmp_dir(args.verify_only)
        ensure_python_313(uv_bin, args.verify_only)
        verify_mcp_stdio(uvx_bin, args.profile, args.region)
    except Exception as exc:
        fail(str(exc))
        return 1

    server_entry = build_server_entry(uvx_bin, args.profile, args.region, oci_config_path)
    targets = resolve_client_targets(args.client, args.target_config)
    for target in targets:
        merge_mcp_config(target, server_entry, args.verify_only)

    valid_oci, oci_msg = validate_oci_config(oci_config_path, args.profile)
    if valid_oci:
        ok(oci_msg)
        verify_oci_api_auth(uvx_bin, oci_config_path, args.profile, args.region)
    else:
        warn(oci_msg)
        if not args.non_interactive and not args.verify_only and sys.stdin.isatty():
            if interactive_oci_setup(oci_config_path, args.profile):
                valid_after, msg_after = validate_oci_config(oci_config_path, args.profile)
                if valid_after:
                    ok(msg_after)
                    verify_oci_api_auth(uvx_bin, oci_config_path, args.profile, args.region)
                else:
                    warn(msg_after)
        else:
            info(
                "Once you download your OCI API key from Oracle Cloud Console and save "
                f"{oci_config_path}, re-run with --verify-only to confirm live OCI auth."
            )

    print()
    ok("Setup complete! In Antigravity IDE: MCP Servers -> Manage MCP Servers -> Refresh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
