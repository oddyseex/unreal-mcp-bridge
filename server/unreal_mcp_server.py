"""Unreal Engine MCP server.

Exposes Unreal Engine 5 to any MCP client (Claude Desktop, Cursor, Muse,
...) as a set of tools. This process speaks MCP over stdio (or Streamable
HTTP with --http); it forwards every tool call to Unreal's built-in
**Remote Control API** over HTTP (default 127.0.0.1:30010).

No custom Unreal plugin or in-engine code is needed. In the UE editor:

    1. Edit -> Plugins -> enable "Remote Control API"
    2. In the editor console:  WebControl.StartServer
       (or run WebControl.EnableServerOnStartup once to make it automatic)

Prerequisites:
    pip install -r requirements.txt   (the ``mcp`` Python SDK)

Run:
    python unreal_mcp_server.py [--http] [--host 127.0.0.1] [--port 8000]

Unreal conventions the AI client must know (in every relevant docstring):
    - Distances are in CENTIMETERS (Unreal units), not meters.
    - Rotations are Pitch/Yaw/Roll, not XYZ euler.
    - Objects are addressed by path, e.g.
      "/Game/Maps/MyMap.MyMap:PersistentLevel.MyActor".
    - Blueprint classes need the _C suffix:
      "/Game/Blueprints/BP_Cube.BP_Cube_C".
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

try:  # FastMCP image helper (unused here, kept for API parity)
    from mcp.server.fastmcp.utilities.types import Image as FastMCPImage  # noqa: F401
except ImportError:  # pragma: no cover - older SDKs
    FastMCPImage = None

from unreal_rc_client import (
    EDITOR_ACTOR_SUBSYSTEM,
    RemoteControlClient,
    UnrealBridgeError,
)

mcp = FastMCP("unreal-bridge")

# One shared client; tests replace this with a mock.
rc = RemoteControlClient()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _location(x: float = 0, y: float = 0, z: float = 0) -> dict:
    return {"X": float(x), "Y": float(y), "Z": float(z)}


def _rotation(pitch: float = 0, yaw: float = 0, roll: float = 0) -> dict:
    return {"Pitch": float(pitch), "Yaw": float(yaw), "Roll": float(roll)}


def _parse_vec3(value, what: str) -> dict:
    if value is None:
        return None
    if isinstance(value, dict):
        return _location(value.get("x", value.get("X", 0)),
                         value.get("y", value.get("Y", 0)),
                         value.get("z", value.get("Z", 0)))
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return _location(*value)
    raise ValueError(f"{what} must be [x, y, z] or {{x, y, z}} (centimeters)")


def _parse_rot(value) -> dict:
    if value is None:
        return None
    if isinstance(value, dict):
        return _rotation(value.get("pitch", value.get("Pitch", 0)),
                         value.get("yaw", value.get("Yaw", 0)),
                         value.get("roll", value.get("Roll", 0)))
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return _rotation(*value)
    raise ValueError("rotation must be [pitch, yaw, roll] or {pitch, yaw, roll}")


# ---------------------------------------------------------------------------
# connection / introspection
# ---------------------------------------------------------------------------

@mcp.tool()
def ping_unreal() -> dict:
    """Check that Unreal's Remote Control API is reachable. Returns server
    info. Call this first; every other tool needs the RC server running
    (editor console: WebControl.StartServer)."""
    try:
        return rc.ping()
    except UnrealBridgeError as exc:
        return {"reachable": False, "error": str(exc)}


@mcp.tool()
def list_actors() -> list:
    """List all actors in the current level (editor). Returns actor object
    paths suitable for the other tools."""
    result = rc.call_function(EDITOR_ACTOR_SUBSYSTEM, "GetAllLevelActors")
    if isinstance(result, dict) and "ReturnValue" in result:
        return result["ReturnValue"]
    return result


@mcp.tool()
def get_actor_info(actor_path: str) -> dict:
    """Describe an actor: its properties and callable functions.

    actor_path looks like "/Game/Maps/MyMap.MyMap:PersistentLevel.MyActor".
    """
    return rc.describe(actor_path)


@mcp.tool()
def search_assets(query: str = "", class_names: list | None = None,
                  package_paths: list | None = None) -> list:
    """Search Unreal's asset registry by name text, class names
    (e.g. ["StaticMesh"]), or package paths (e.g. ["/Game/Props"])."""
    return rc.search_assets(query=query, class_names=class_names,
                            package_paths=package_paths)


# ---------------------------------------------------------------------------
# actors
# ---------------------------------------------------------------------------

@mcp.tool()
def spawn_actor(actor_class: str = "/Script/Engine.StaticMeshActor",
                location: list | dict | None = None,
                rotation: list | dict | None = None) -> dict:
    """Spawn an actor in the current level (editor).

    actor_class: class path. Native: "/Script/Engine.StaticMeshActor",
    "/Script/Engine.PointLight", ... Blueprint: "/Game/Blueprints/BP_Cube.BP_Cube_C"
    (note the _C suffix).
    location: [x, y, z] in CENTIMETERS. rotation: [pitch, yaw, roll] degrees.
    Returns the RC response (includes the spawned actor reference).
    """
    params = {
        "ActorClass": actor_class,
        "Location": _parse_vec3(location, "location") or _location(),
        "Rotation": _parse_rot(rotation) or _rotation(),
    }
    return rc.call_function(EDITOR_ACTOR_SUBSYSTEM, "SpawnActorFromClass", params)


@mcp.tool()
def set_actor_transform(actor_path: str,
                        location: list | dict | None = None,
                        rotation: list | dict | None = None) -> dict:
    """Move / rotate an actor. location in CENTIMETERS [x, y, z];
    rotation in degrees [pitch, yaw, roll]. Only what you pass is changed."""
    results = {}
    if location is not None:
        results["set_location"] = rc.call_function(
            actor_path, "SetActorLocation",
            {"NewLocation": _parse_vec3(location, "location"),
             "bSweep": False, "bTeleport": True})
    if rotation is not None:
        results["set_rotation"] = rc.call_function(
            actor_path, "SetActorRotation",
            {"NewRotation": _parse_rot(rotation), "bTeleport": True})
    if not results:
        raise ValueError("pass location and/or rotation")
    return results


@mcp.tool()
def delete_actor(actor_path: str) -> dict:
    """Delete an actor from the level (editor). Returns the RC response."""
    return rc.call_function(EDITOR_ACTOR_SUBSYSTEM, "DestroyActor",
                            {"ActorToDestroy": actor_path})


# ---------------------------------------------------------------------------
# raw power tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_property(object_path: str, property_name: str | None = None):
    """Read a property on any UObject (omit property_name to read all)."""
    return rc.get_property(object_path, property_name)


@mcp.tool()
def set_property(object_path: str, property_name: str, value) -> dict:
    """Write a property on any UObject. Returns the RC API response."""
    return rc.set_property(object_path, property_name, value)


@mcp.tool()
def call_function(object_path: str, function_name: str,
                   parameters: dict | None = None):
    """Call any BlueprintCallable UFUNCTION on any UObject.

    The raw power tool — equivalent to Blender's execute_blender_code.
    parameters is a dict of argument name -> value, e.g.
    {"NewLocation": {"X": 0, "Y": 0, "Z": 100}}.
    """
    return rc.call_function(object_path, function_name, parameters)


@mcp.tool()
def execute_batch(requests: list[dict]) -> list:
    """Execute several Remote Control requests in ONE round trip.

    Each item: {"url": "/remote/object/property", "verb": "PUT", "body": {...}}.
    Returns the per-request responses in order.
    """
    if not isinstance(requests, list) or not requests:
        raise ValueError("requests must be a non-empty list")
    for r in requests:
        if not isinstance(r, dict) or "url" not in r or "body" not in r:
            raise ValueError("each request needs 'url' and 'body'")
    return rc.batch(requests)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Unreal Engine MCP Bridge (MCP -> HTTP 127.0.0.1:30010 -> UE Remote Control API)"
    )
    parser.add_argument(
        "--http", action="store_true",
        help="serve over Streamable HTTP instead of stdio",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address for --http (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8001,
                        help="port for --http (default: 8001)")
    parser.add_argument("--rc-host", default=None,
                        help="Unreal RC host (default: 127.0.0.1 or UNREAL_RC_HOST)")
    parser.add_argument("--rc-port", type=int, default=None,
                        help="Unreal RC port (default: 30010 or UNREAL_RC_PORT)")
    args = parser.parse_args()

    if args.rc_host or args.rc_port:
        from unreal_rc_client import RC_HOST, RC_PORT
        rc = RemoteControlClient(host=args.rc_host or RC_HOST,
                                 port=args.rc_port or RC_PORT)

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        print(f"[unreal-mcp] serving Streamable HTTP on {args.host}:{args.port}/mcp")
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio: what Claude Desktop / Cursor / Muse CLI expect
