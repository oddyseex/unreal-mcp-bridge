# Unreal MCP Bridge

Drive **Unreal Engine 5** from any AI assistant over the **Model Context
Protocol**. An MCP server (this repo) exposes the editor as tools, speaking
to Unreal's **built-in Remote Control API** — no custom plugin or in-engine
code needed.

```
MCP client (Claude Desktop / Cursor / Muse / ...)
   --stdio-->  server/unreal_mcp_server.py
   --HTTP 127.0.0.1:30010-->  UE Remote Control API  -->  editor
```

Part of the bridge family: [`blender-mcp-bridge`](https://github.com/oddyseex/blender-mcp-bridge)
shares the same tool names and conventions.

## What you can do with it

- `"Spawn a cube at (300, 0, 100)"` → `spawn_actor`
- `"List everything in the level"` → `list_actors`
- `"Move that light up 500 units"` → `set_actor_transform`
- `"Find all static meshes named 'Wall'"` → `search_assets`
- `"Set that actor's mobility to movable"` → `set_property`
- Anything else → `call_function` (the raw power tool)

## Setup

### 1. Enable Remote Control in Unreal

1. In the UE editor: **Edit → Plugins** → search **"Remote Control API"** → Enable it (restart the editor).
2. Open the editor console ( <code>&#96;</code> key) and run:
   ```
   WebControl.StartServer
   ```
   To make it automatic, run once: `WebControl.EnableServerOnStartup`
3. Verify: open `http://127.0.0.1:30010/remote/info` in a browser — you should
   see JSON, not a connection error.

The editor must be open with a level loaded while you use the bridge.
`EditorActorSubsystem` tools (spawn/list/delete actors) need the **editor** —
they don't work in a packaged game.

### 2. Install the MCP server

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Windows users: extract the release zip and double-click **`run_server.bat`**
(it creates the venv, installs deps, and starts the server for a sanity check).

### 3. Point your AI client at it

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "unreal": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/server/unreal_mcp_server.py"]
    }
  }
}
```

**Cursor**: Settings → MCP → Add Server → same command/args.

**Muse CLI**: `Muse mcp add unreal -- /absolute/path/to/.venv/bin/python /absolute/path/to/server/unreal_mcp_server.py`

Then ask it to run `ping_unreal` — if it reports the RC server info, you're live.

### Shared-server mode (optional)

```bash
python server/unreal_mcp_server.py --http --port 8001
# serves MCP at http://127.0.0.1:8001/mcp
```

One server, many clients. If Unreal runs on a different host/port:
`--rc-host` / `--rc-port` (or env `UNREAL_RC_HOST` / `UNREAL_RC_PORT`).

## Tools

| Tool | What it does |
|---|---|
| `ping_unreal` | Health check; returns RC server info |
| `list_actors` | All actors in the current level |
| `get_actor_info` | Describe an actor: properties + callable functions |
| `search_assets` | Search the asset registry (name / class / package path) |
| `spawn_actor` | Spawn an actor (native class or Blueprint) in the level |
| `set_actor_transform` | Move / rotate an actor |
| `delete_actor` | Delete an actor from the level |
| `get_property` | Read a property on any UObject |
| `set_property` | Write a property on any UObject |
| `call_function` | Call any BlueprintCallable UFUNCTION (raw power tool) |
| `execute_batch` | Several RC requests in one round trip |

## Unreal conventions (the AI must know these)

- **Units are centimeters.** `location=[0, 0, 500]` is 5 meters up.
- **Rotation is Pitch/Yaw/Roll** in degrees — not XYZ euler.
- **Objects are addressed by path**, e.g.
  `/Game/Maps/MyMap.MyMap:PersistentLevel.MyActor`.
- **Blueprint classes need the `_C` suffix**:
  `/Game/Blueprints/BP_Cube.BP_Cube_C`. Native classes look like
  `/Script/Engine.StaticMeshActor`.

## Limitations (honest)

- **No viewport screenshots.** The Remote Control API doesn't expose one.
  Use UE's `HighResShot` console command manually when you need a visual check.
- Actor tools target the **editor**. Packaged builds only expose what your
  game's Remote Control presets expose.
- Function parameter shapes follow UE's reflection; when in doubt, ask the AI
  to `get_actor_info` / `describe` first — it lists exact function signatures.

## Troubleshooting

- **"cannot reach Unreal Remote Control at 127.0.0.1:30010"** → editor isn't
  open, the Remote Control API plugin isn't enabled, or you didn't run
  `WebControl.StartServer`.
- **Port already in use** → another UE instance (or something else) holds 30010.
- **Empty/missing actors** → make sure a level is loaded in the editor.

## Security

The bridge can call any exposed UE function, including spawning and deleting
actors. It binds to localhost only. Never forward port 30010 to a network.

## Project layout

```
server/unreal_rc_client.py   # stdlib-only HTTP client for the RC API
server/unreal_mcp_server.py   # the MCP server (FastMCP tools over stdio)
tests/test_rc_client.py       # client tests against a mock RC server
tests/test_mcp_server.py      # tool registration + RC call-shape tests
```
