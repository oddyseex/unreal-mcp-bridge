"""HTTP client for Unreal Engine's built-in Remote Control API.

The Remote Control API is a REST interface shipped with Unreal Engine 5
(no custom plugin or in-engine code needed). Enable it in the editor:

    Edit -> Plugins -> "Remote Control API" (enable)
    Then in the editor console:  WebControl.StartServer
    (or: WebControl.EnableServerOnStartup, to start it automatically)

Default endpoint: http://127.0.0.1:30010
Override with env vars: UNREAL_RC_HOST, UNREAL_RC_PORT, UNREAL_RC_PASSPHRASE

Request/response shapes below were verified against Epic's RC HTTP API and
the upyrc / unreal-rc client implementations.

Endpoints used:
    GET  /remote/info               server info
    PUT  /remote/object/property    get/set UObject properties
    PUT  /remote/object/call        call UFUNCTIONs
    PUT  /remote/object/describe    describe a UObject
    PUT  /remote/search/assets      search the asset registry
    PUT  /remote/batch              batch several requests in one round trip
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

RC_HOST = os.environ.get("UNREAL_RC_HOST", "127.0.0.1")
RC_PORT = int(os.environ.get("UNREAL_RC_PORT", "30010"))
RC_PASSPHRASE = os.environ.get("UNREAL_RC_PASSPHRASE")  # optional
DEFAULT_TIMEOUT = 30.0

# Well-known editor utility objects (Default__ instances are always present).
EDITOR_ACTOR_SUBSYSTEM = "/Script/UnrealEd.Default__EditorActorSubsystem"
EDITOR_ASSET_LIBRARY = "/Script/EditorScriptingUtilities.Default__EditorAssetLibrary"


class UnrealBridgeError(Exception):
    """Raised when Unreal is unreachable or the RC API reports an error."""


class RemoteControlClient:
    """Thin wrapper over the Remote Control HTTP API (stdlib only)."""

    def __init__(self, host: str = RC_HOST, port: int = RC_PORT,
                 timeout: float = DEFAULT_TIMEOUT,
                 passphrase: str | None = RC_PASSPHRASE):
        self.base_url = f"http://{host}:{port}"
        self.host, self.port = host, port
        self.timeout = timeout
        self.passphrase = passphrase

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------

    def _request(self, method: str, route: str, body: dict | None = None):
        url = f"{self.base_url}/{route.lstrip('/')}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "unreal-mcp-bridge")
        if self.passphrase:
            req.add_header("Passphrase", self.passphrase)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise UnrealBridgeError(self._extract_error(exc)) from exc
        except urllib.error.URLError as exc:
            raise UnrealBridgeError(
                f"cannot reach Unreal Remote Control at {self.host}:{self.port} "
                f"(is the editor open with the Remote Control API plugin enabled "
                f"and WebControl.StartServer run? {exc.reason})"
            ) from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise UnrealBridgeError(f"invalid JSON from Unreal: {exc}") from exc

    @staticmethod
    def _extract_error(exc: urllib.error.HTTPError) -> str:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            msg = payload.get("errorMessage") or payload.get("message")
            if msg:
                return f"Unreal RC API error (HTTP {exc.code}): {msg}"
        except Exception:  # noqa: BLE001 - fall through to generic message
            pass
        if exc.code == 404:
            return f"Unreal RC API error (HTTP 404): unknown route or object"
        return f"Unreal RC API error (HTTP {exc.code}): {exc.reason}"

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def info(self) -> dict:
        """Server info. Raises UnrealBridgeError if unreachable."""
        result = self._request("GET", "remote/info")
        return result if isinstance(result, dict) else {"info": result}

    def ping(self) -> dict:
        info = self.info()
        info["reachable"] = True
        return info

    def get_property(self, object_path: str, property_name: str | None = None):
        """Read one property (or all properties when property_name is None)."""
        body: dict = {"objectPath": object_path, "access": "READ_ACCESS"}
        if property_name:
            body["propertyName"] = property_name
        result = self._request("PUT", "remote/object/property", body)
        if isinstance(result, dict) and property_name and property_name in result:
            return result[property_name]
        return result

    def set_property(self, object_path: str, property_name: str, value) -> dict:
        """Write one property. Returns the RC API response."""
        body = {
            "objectPath": object_path,
            "access": "WRITE_ACCESS",
            "propertyName": property_name,
            "propertyValue": value,
        }
        return self._request("PUT", "remote/object/property", body)

    def call_function(self, object_path: str, function_name: str,
                      parameters: dict | None = None):
        """Call a UFUNCTION on a UObject. Returns the parsed response
        (usually contains a ReturnValue key)."""
        body: dict = {
            "objectPath": object_path,
            "access": "WRITE_ACCESS",
            "functionName": function_name,
        }
        if parameters:
            body["parameters"] = parameters
        return self._request("PUT", "remote/object/call", body)

    def describe(self, object_path: str) -> dict:
        """Describe a UObject: its properties and callable functions."""
        return self._request("PUT", "remote/object/describe",
                             {"objectPath": object_path})

    def search_assets(self, query: str = "", class_names: list | None = None,
                      package_paths: list | None = None) -> list:
        """Search the asset registry. Returns the asset list."""
        body = {
            "Query": query,
            "Filter": {
                "PackageNames": [],
                "ClassNames": class_names or [],
                "PackagePaths": package_paths or [],
                "RecursiveClassesExclusionSet": [],
                "RecursivePaths": True,
                "RecursiveClasses": False,
            },
        }
        result = self._request("PUT", "remote/search/assets", body)
        if isinstance(result, dict) and "Assets" in result:
            return result["Assets"]
        return result

    def batch(self, requests: list[dict]) -> list[dict]:
        """Execute several RC requests in one round trip.

        requests: [{"url": "/remote/object/property", "verb": "PUT",
                    "body": {...}}, ...]
        Returns the list of per-request responses.
        """
        if not requests:
            raise UnrealBridgeError("batch requires at least one request")
        payload = {"Requests": [
            {"RequestId": i + 1,
             "URL": r["url"],
             "Verb": r.get("verb", "PUT").upper(),
             "Body": r.get("body", {})}
            for i, r in enumerate(requests)
        ]}
        result = self._request("PUT", "remote/batch", payload)
        if isinstance(result, dict) and "Responses" in result:
            return result["Responses"]
        return result
