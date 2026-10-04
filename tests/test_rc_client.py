"""Tests for unreal_rc_client against a mock Remote Control HTTP server.

No Unreal Engine needed: a local http.server emulates the RC API routes and
verifies request bodies, so the client's framing, parsing, and error mapping
are exercised for real.

Run:  python3 tests/test_rc_client.py
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/home/hatch/workspace/unreal-mcp-bridge/server")

from unreal_rc_client import RemoteControlClient, UnrealBridgeError  # noqa: E402

MOCK_PORT = 30099
SEEN = []  # (method, path, body) tuples the mock received


class MockRC(BaseHTTPRequestHandler):
    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        SEEN.append(("GET", self.path, None))
        if self.path == "/remote/info":
            self._json(200, {"name": "MockRC", "version": "5.5"})
        else:
            self._json(404, {"errorMessage": f"no such route: {self.path}"})

    def do_PUT(self):
        body = self._body()
        SEEN.append(("PUT", self.path, body))
        if self.path == "/remote/object/property":
            if body.get("access") == "WRITE_ACCESS":
                self._json(200, {"Set": True})
            elif "propertyName" in body:
                self._json(200, {body["propertyName"]: {"X": 1.0, "Y": 2.0, "Z": 3.0}})
            else:
                self._json(200, {"PropA": 1, "PropB": "two"})
        elif self.path == "/remote/object/call":
            self._json(200, {"ReturnValue": f"called:{body.get('functionName')}"})
        elif self.path == "/remote/object/describe":
            self._json(200, {"Properties": ["PropA"], "Functions": ["FuncB"]})
        elif self.path == "/remote/search/assets":
            self._json(200, {"Assets": [{"ObjectPath": "/Game/SM_Cube"}]})
        elif self.path == "/remote/batch":
            self._json(200, {"Responses": [
                {"RequestId": r["RequestId"], "ResponseCode": 200,
                 "ResponseBody": {"ok": True}}
                for r in body["Requests"]]})
        elif self.path == "/remote/object/boom":
            self._json(500, {"errorMessage": "kaboom in editor"})
        else:
            self._json(404, {"errorMessage": f"no such route: {self.path}"})

    def log_message(self, *args):  # quiet
        pass


def run_tests():
    server = HTTPServer(("127.0.0.1", MOCK_PORT), MockRC)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rc = RemoteControlClient(port=MOCK_PORT, timeout=5)

    # 1. ping / info
    info = rc.ping()
    assert info["reachable"] is True and info["name"] == "MockRC", info
    print("PASS: ping/info round trip")

    # 2. get single property -> value extracted by key
    v = rc.get_property("/Some/Path", "RelativeLocation")
    assert v == {"X": 1.0, "Y": 2.0, "Z": 3.0}, v
    method, path, body = SEEN[-1]
    assert body["access"] == "READ_ACCESS" and body["propertyName"] == "RelativeLocation"
    print("PASS: get_property (single, value extracted)")

    # 3. get all properties
    v = rc.get_property("/Some/Path")
    assert v == {"PropA": 1, "PropB": "two"}, v
    assert "propertyName" not in SEEN[-1][2]
    print("PASS: get_property (all)")

    # 4. set property body shape
    r = rc.set_property("/Some/Path", "bHidden", True)
    assert r == {"Set": True}, r
    _, _, body = SEEN[-1]
    assert body["access"] == "WRITE_ACCESS" and body["propertyValue"] is True
    print("PASS: set_property body shape")

    # 5. call function passes parameters through
    r = rc.call_function("/Some/Path", "SetActorLocation",
                         {"NewLocation": {"X": 0, "Y": 0, "Z": 100}})
    assert r == {"ReturnValue": "called:SetActorLocation"}, r
    _, _, body = SEEN[-1]
    assert body["parameters"]["NewLocation"]["Z"] == 100
    print("PASS: call_function parameter passthrough")

    # 6. describe
    d = rc.describe("/Some/Path")
    assert d["Functions"] == ["FuncB"], d
    print("PASS: describe")

    # 7. search assets -> Assets list
    assets = rc.search_assets(query="Cube", class_names=["StaticMesh"])
    assert assets == [{"ObjectPath": "/Game/SM_Cube"}], assets
    _, _, body = SEEN[-1]
    assert body["Filter"]["ClassNames"] == ["StaticMesh"]
    print("PASS: search_assets")

    # 8. batch
    resps = rc.batch([
        {"url": "/remote/object/property", "verb": "PUT",
         "body": {"objectPath": "a", "access": "READ_ACCESS"}},
        {"url": "/remote/object/call", "body": {"objectPath": "b"}},
    ])
    assert len(resps) == 2 and all(r["ResponseCode"] == 200 for r in resps), resps
    _, _, body = SEEN[-1]
    assert body["Requests"][0]["RequestId"] == 1
    assert body["Requests"][1]["Verb"] == "PUT"  # default verb
    print("PASS: batch (ids assigned, verb defaulted)")

    # 9. HTTP 500 -> UnrealBridgeError with server message
    try:
        rc._request("PUT", "remote/object/boom", {})
    except UnrealBridgeError as e:
        assert "kaboom in editor" in str(e), e
        print(f"PASS: server error mapped ({e})")
    else:
        raise AssertionError("expected UnrealBridgeError")

    # 10. 404 route -> UnrealBridgeError
    try:
        rc._request("GET", "remote/nope")
    except UnrealBridgeError as e:
        assert "404" in str(e), e
        print("PASS: 404 mapped")

    # 11. connection refused -> helpful UnrealBridgeError
    bad = RemoteControlClient(port=39999, timeout=2)
    try:
        bad.ping()
    except UnrealBridgeError as e:
        assert "39999" in str(e) and "WebControl.StartServer" in str(e), e
        print("PASS: connection-refused guidance")
    else:
        raise AssertionError("expected UnrealBridgeError")

    server.shutdown()
    print("\nAll RC client tests passed.")


if __name__ == "__main__":
    run_tests()
