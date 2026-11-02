import os
import json
import urllib.request
import urllib.error
import tempfile
import threading
import pytest
from server import load_user_favorites, save_user_favorites, FAVORITES_FILE, favorites_lock


def test_load_save_favorites(tmp_path, monkeypatch):
    test_file = str(tmp_path / "user_favorites.json")
    monkeypatch.setattr("server.FAVORITES_FILE", test_file)

    assert load_user_favorites() == {}

    data = {
        "0xa15bb4aedc1b3426159c3cacf8548cf09506a20a": {
            "favorites": ["0x105d904d64520d9bcefb66af936c2cce59037777"],
            "updated_at": 1730000000
        }
    }
    save_user_favorites(data)
    loaded = load_user_favorites()
    assert loaded == data
    assert "0xa15bb4aedc1b3426159c3cacf8548cf09506a20a" in loaded


def test_concurrent_favorites_write(tmp_path, monkeypatch):
    test_file = str(tmp_path / "user_favorites_conc.json")
    monkeypatch.setattr("server.FAVORITES_FILE", test_file)

    def worker(idx):
        wallet = f"0x{idx:040x}"
        with favorites_lock:
            cur = load_user_favorites()
            cur[wallet] = {"favorites": [f"0x{idx+1:040x}"], "updated_at": idx}
            save_user_favorites(cur)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    res = load_user_favorites()
    assert len(res) == 10

def test_http_favorites_endpoints(tmp_path, monkeypatch):
    import socket
    from server import AgentIdServerHandler, ThreadingHTTPServer

    test_file = str(tmp_path / "user_favorites_http.json")
    monkeypatch.setattr("server.FAVORITES_FILE", test_file)

    # Find free port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = ThreadingHTTPServer(("127.0.0.1", port), AgentIdServerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    wallet = "0xa15bb4aedc1b3426159c3cacf8548cf09506a20a"

    # 1. GET with missing wallet
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{base_url}/api/favorites")
    assert exc_info.value.code == 400

    # 2. GET empty favorites
    res = urllib.request.urlopen(f"{base_url}/api/favorites?wallet={wallet}")
    assert res.status == 200
    data = json.loads(res.read().decode())
    assert data["success"] is True
    assert data["favorites"] == []

    # 3. POST save favorites
    fav_payload = {
        "wallet": wallet,
        "favorites": ["0x105d904d64520d9bcefb66af936c2cce59037777", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"],
        "timestamp": 1730500000
    }
    req = urllib.request.Request(
        f"{base_url}/api/favorites",
        data=json.dumps(fav_payload).encode(),
        headers={"Content-Type": "application/json"}
    )
    post_res = urllib.request.urlopen(req)
    assert post_res.status == 200
    post_data = json.loads(post_res.read().decode())
    assert post_data["success"] is True
    assert len(post_data["favorites"]) == 2

    # 4. GET updated favorites
    get_res = urllib.request.urlopen(f"{base_url}/api/favorites?wallet={wallet}")
    get_data = json.loads(get_res.read().decode())
    assert get_data["favorites"] == ["0x105d904d64520d9bcefb66af936c2cce59037777", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"]
    assert get_data["updated_at"] == 1730500000

    server.shutdown()


def test_cross_device_sync_scenario(tmp_path, monkeypatch):
    import socket
    from server import AgentIdServerHandler, ThreadingHTTPServer

    test_file = str(tmp_path / "user_favorites_cross_device.json")
    monkeypatch.setattr("server.FAVORITES_FILE", test_file)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = ThreadingHTTPServer(("127.0.0.1", port), AgentIdServerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    wallet = "0x6827d403df6b07ba405a6cfe0305dbd0fc727f55"

    # Step 1: Desktop user stars 2 tokens
    desktop_favs = ["0x105d904d64520d9bcefb66af936c2cce59037777", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"]
    req1 = urllib.request.Request(
        f"{base_url}/api/favorites",
        data=json.dumps({"wallet": wallet, "favorites": desktop_favs, "timestamp": 1000}).encode(),
        headers={"Content-Type": "application/json"}
    )
    res1 = urllib.request.urlopen(req1)
    assert res1.status == 200

    # Step 2: Mobile user opens app with same wallet, fetches favorites
    res_mobile = urllib.request.urlopen(f"{base_url}/api/favorites?wallet={wallet}")
    mobile_data = json.loads(res_mobile.read().decode())
    assert set(mobile_data["favorites"]) == set(desktop_favs)

    # Step 3: Mobile user stars 1 new token and pushes merged list
    mobile_favs = list(set(mobile_data["favorites"]) | {"0x55d398326f99059ff775485246999027b3197955"})
    req2 = urllib.request.Request(
        f"{base_url}/api/favorites",
        data=json.dumps({"wallet": wallet, "favorites": mobile_favs, "timestamp": 2000}).encode(),
        headers={"Content-Type": "application/json"}
    )
    res2 = urllib.request.urlopen(req2)
    assert res2.status == 200

    # Step 4: Desktop user refreshes or auto-polls, sees all 3 tokens
    res_desktop_sync = urllib.request.urlopen(f"{base_url}/api/favorites?wallet={wallet}")
    final_data = json.loads(res_desktop_sync.read().decode())
    assert len(final_data["favorites"]) == 3
    assert "0x55d398326f99059ff775485246999027b3197955" in final_data["favorites"]

    server.shutdown()
