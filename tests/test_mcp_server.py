"""Verify the Unreal MCP server: tool registration + RC call shapes.

No Unreal needed: srv.rc is replaced with a recording mock, then the
spawn/transform/delete tools are invoked and the exact Remote Control
requests they would send are asserted.

Run with a venv that has the mcp SDK:
    python tests/test_mcp_server.py
"""

import asyncio
import sys

sys.path.insert(0, "/home/hatch/workspace/unreal-mcp-bridge/server")

import unreal_mcp_server as srv  # noqa: E402


EXPECTED_TOOLS = sorted([
    "ping_unreal",
    "list_actors",
    "get_actor_info",
    "search_assets",
    "spawn_actor",
    "set_actor_transform",
    "delete_actor",
    "get_property",
    "set_property",
    "call_function",
    "execute_batch",
])


class MockRC:
    def __init__(self):
        self.calls = []  # ("call_function", path, func, params) etc.

    def call_function(self, object_path, function_name, parameters=None):
        self.calls.append(("call", object_path, function_name, parameters))
        return {"ReturnValue": f"mock:{function_name}"}

    def describe(self, object_path):
        self.calls.append(("describe", object_path))
        return {"Properties": [], "Functions": []}


def call_tool(fn, *args, **kwargs):
    target = getattr(fn, "fn", fn)
    return target(*args, **kwargs)


def run_tests():
    try:
        tools = asyncio.run(srv.mcp.list_tools())
        names = sorted(t.name for t in tools)
    except AttributeError:
        names = sorted(srv.mcp._tool_manager._tools.keys())
    assert names == EXPECTED_TOOLS, f"\n got: {names}\n want: {EXPECTED_TOOLS}"
    print(f"PASS: {len(names)} tools registered")

    mock = MockRC()
    srv.rc = mock

    # spawn_actor: class path + cm/PitchYawRoll param shapes
    call_tool(srv.spawn_actor, actor_class="/Script/Engine.StaticMeshActor",
              location=[100, 200, 300], rotation=[0, 90, 0])
    kind, path, func, params = mock.calls[-1]
    assert func == "SpawnActorFromClass", func
    assert params["ActorClass"] == "/Script/Engine.StaticMeshActor"
    assert params["Location"] == {"X": 100.0, "Y": 200.0, "Z": 300.0}, params
    assert params["Rotation"] == {"Pitch": 0.0, "Yaw": 90.0, "Roll": 0.0}, params
    print("PASS: spawn_actor RC call shape")

    # dict-form location also accepted
    call_tool(srv.spawn_actor, location={"x": 1, "y": 2, "z": 3})
    _, _, _, params = mock.calls[-1]
    assert params["Location"] == {"X": 1.0, "Y": 2.0, "Z": 3.0}, params
    print("PASS: spawn_actor accepts dict location")

    # set_actor_transform: only location -> only SetActorLocation
    mock.calls.clear()
    call_tool(srv.set_actor_transform, actor_path="/Game/Map:PersistentLevel.Cube",
              location=[0, 0, 500])
    assert len(mock.calls) == 1, mock.calls
    _, _, func, params = mock.calls[0]
    assert func == "SetActorLocation", func
    assert params["NewLocation"] == {"X": 0.0, "Y": 0.0, "Z": 500.0}, params
    assert params["bTeleport"] is True
    print("PASS: set_actor_transform (location only)")

    # rotation only -> only SetActorRotation
    mock.calls.clear()
    call_tool(srv.set_actor_transform, actor_path="p", rotation=[10, 20, 30])
    _, _, func, params = mock.calls[0]
    assert func == "SetActorRotation", func
    assert params["NewRotation"] == {"Pitch": 10.0, "Yaw": 20.0, "Roll": 30.0}
    print("PASS: set_actor_transform (rotation only)")

    # neither -> ValueError before any RC call
    mock.calls.clear()
    try:
        call_tool(srv.set_actor_transform, actor_path="p")
    except ValueError:
        assert mock.calls == []
        print("PASS: set_actor_transform rejects empty call")
    else:
        raise AssertionError("expected ValueError")

    # delete_actor
    call_tool(srv.delete_actor, actor_path="p")
    _, _, func, params = mock.calls[-1]
    assert func == "DestroyActor" and params == {"ActorToDestroy": "p"}, (func, params)
    print("PASS: delete_actor RC call shape")

    # bad vector rejected
    for bad in ("nope", [1, 2], [1, 2, 3, 4]):
        try:
            srv._parse_vec3(bad, "location")
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")
    print("PASS: vector validation")

    # execute_batch validation (no RC call on bad input)
    try:
        call_tool(srv.execute_batch, requests=[])
    except ValueError:
        print("PASS: execute_batch rejects empty list")
    else:
        raise AssertionError("expected ValueError")

    print("\nAll MCP server tests passed.")


if __name__ == "__main__":
    run_tests()
