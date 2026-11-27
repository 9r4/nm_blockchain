"""
管理ページ用サーバー

これ1つを起動するだけで、ブロックチェーンサーバーとユーザ(ウォレットサーバー)を
Web画面から起動・停止できる。既存のコードは変更せず、別プロセスとして起動する。

使い方:
    python admin_server.py           → http://localhost:9000 がブラウザで自動的に開く
    python admin_server.py -u 0      → 最初はユーザを起動しない
    python admin_server.py -h        → オプション一覧

終了: このターミナルで Ctrl + C (起動したサーバーもすべて停止する)
"""

import atexit
import os
import re
import signal
import socket
import string
import subprocess
import sys
import threading
import time
import webbrowser

import requests
from flask import Flask, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
BLOCKCHAIN_PORT = 5000
FIRST_WALLET_PORT = 8080
MAX_USERS = 20
IS_WINDOWS = os.name == "nt"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# 管理画面が自動更新のために送っているアクセスのログ
POLLING_LOG = re.compile(r'"GET /(chain|transaction) HTTP/[0-9.]+" 200')

app = Flask(__name__, template_folder="./templates")
lock = threading.RLock()


def is_port_in_use(port):
    """そのポートで何かが待ち受けているか(接続できるか)を調べる"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def call_blockchain(path, timeout=2):
    """ブロックチェーンサーバーへGETする(PCのプロキシ設定は無視して直接接続)"""
    with requests.Session() as session:
        session.trust_env = False
        return session.get(f"http://127.0.0.1:{BLOCKCHAIN_PORT}{path}", timeout=timeout)


def read_tail(path, lines=200, hide_polling=False):
    """ログファイルの末尾を読む"""
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 200_000))
        data = f.read().decode("utf-8", errors="replace")
    data = ANSI_ESCAPE.sub("", data)  # 文字色の制御コードを取り除く
    rows = data.splitlines()
    if hide_polling:
        rows = [row for row in rows if not POLLING_LOG.search(row)]
    return "\n".join(rows[-lines:])


class ManagedProcess(object):
    """管理ページから起動する1つのサーバー(プロセス)"""

    def __init__(self, key, name, script, port, extra_args=()):
        self.key = key
        self.name = name
        self.script = script
        self.port = port
        self.extra_args = list(extra_args)
        self.proc = None
        self.log_file = None
        self.started_at = None
        self.exit_code = None
        self.log_path = os.path.join(LOG_DIR, f"{key}.log")

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.is_alive():
            return
        os.makedirs(LOG_DIR, exist_ok=True)
        self.close_log()
        self.log_file = open(self.log_path, "w", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"  # ログをすぐファイルに書き出す
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [sys.executable, self.script, "-p", str(self.port), *self.extra_args]
        kwargs = {}
        if not IS_WINDOWS:
            # 停止時にデバッグ用の子プロセスごとまとめて終了できるようにする
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(
            cmd,
            cwd=BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            env=env,
            **kwargs,
        )
        self.started_at = time.time()
        self.exit_code = None

    def stop(self):
        proc = self.proc
        if proc is None:
            return
        # debug=True のサーバーは子プロセス(自動リロード用)を作るため、まとめて終了させる
        if IS_WINDOWS:
            if proc.poll() is None:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        else:
            self._kill_group(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        if not IS_WINDOWS:
            self._kill_group(signal.SIGKILL)  # 取り残された子プロセスがあれば確実に終了
        self.exit_code = proc.returncode
        self.proc = None
        self.started_at = None
        self.close_log()

    def _kill_group(self, sig):
        try:
            os.killpg(self.proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def close_log(self):
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None

    def status(self):
        if self.proc is None:
            return "stopped"
        code = self.proc.poll()
        if code is not None:
            self.exit_code = code
            return "exited"  # エラーなどで終了した
        return "running" if is_port_in_use(self.port) else "starting"

    def to_dict(self):
        status = self.status()
        return {
            "key": self.key,
            "name": self.name,
            "port": self.port,
            "status": status,
            "pid": self.proc.pid if self.proc else None,
            "uptime": int(time.time() - self.started_at)
            if status in ("running", "starting")
            else None,
            "exit_code": self.exit_code if status == "exited" else None,
            "url": f"http://localhost:{self.port}/",
        }


blockchain_proc = ManagedProcess(
    "blockchain", "Blockchain Node", "blockchain_server.py", BLOCKCHAIN_PORT
)
users = {}  # ポート番号 -> ManagedProcess
auto_mining = {"enabled": False}


def reserved_ports():
    return {BLOCKCHAIN_PORT, app.config.get("admin_port", 9000)}


def next_user_name():
    used = {u.name for u in users.values()}
    for letter in string.ascii_uppercase:
        name = f"ユーザ{letter}"
        if name not in used:
            return name
    return f"ユーザ{len(users) + 1}"


def next_free_port():
    port = FIRST_WALLET_PORT
    while port in users or port in reserved_ports() or is_port_in_use(port):
        port += 1
    return port


def add_user(name=None, port=None):
    """ユーザを追加して起動する。失敗時は (None, エラーメッセージ) を返す"""
    with lock:
        if len(users) >= MAX_USERS:
            return None, f"ユーザは最大{MAX_USERS}人までです"
        if port is None:
            port = next_free_port()
        else:
            if not 1024 <= port <= 65535:
                return None, "ポート番号は1024〜65535で指定してください"
            if port in users or port in reserved_ports():
                return None, f"ポート{port}は既に使われています"
            if is_port_in_use(port):
                return (
                    None,
                    f"ポート{port}は他のプログラムが使用中です(手動で起動したサーバーが残っていないか確認してください)",
                )
        name = (name or "").strip()[:20] or next_user_name()
        user = ManagedProcess(
            f"wallet_{port}",
            name,
            "wallet_server.py",
            port,
            extra_args=["-g", f"http://127.0.0.1:{BLOCKCHAIN_PORT}"],
        )
        user.start()
        users[port] = user
        return user, None


def start_blockchain():
    with lock:
        if blockchain_proc.is_alive():
            return None
        if is_port_in_use(BLOCKCHAIN_PORT):
            return f"ポート{BLOCKCHAIN_PORT}は他のプログラムが使用中です(手動で起動したblockchain_server.pyを止めてください)"
        blockchain_proc.start()
        auto_mining["enabled"] = False
        return None


def stop_all():
    with lock:
        for user in list(users.values()):
            user.stop()
        blockchain_proc.stop()


# ===================== 画面 =====================


@app.route("/")
def admin_page():
    return render_template("admin.html")


# ===================== API =====================


@app.route("/api/status", methods=["GET"])
def api_status():
    with lock:
        node = blockchain_proc.to_dict()
        user_list = [u.to_dict() for u in users.values()]
    node["auto_mining"] = auto_mining["enabled"]
    chain, pool = None, None
    if node["status"] == "running":
        try:
            chain = call_blockchain("/chain").json().get("chain")
            pool = call_blockchain("/transaction").json().get("transactions")
        except (requests.exceptions.RequestException, ValueError):
            pass
    return jsonify(
        {"blockchain": node, "users": user_list, "chain": chain, "pool": pool}
    ), 200


@app.route("/api/blockchain/start", methods=["POST"])
def api_blockchain_start():
    error = start_blockchain()
    if error:
        return jsonify({"message": error}), 409
    return jsonify({"message": "ブロックチェーンサーバーを起動しました"}), 200


@app.route("/api/blockchain/stop", methods=["POST"])
def api_blockchain_stop():
    with lock:
        blockchain_proc.stop()
        auto_mining["enabled"] = False
    return jsonify({"message": "ブロックチェーンサーバーを停止しました"}), 200


@app.route("/api/mine", methods=["POST"])
def api_mine():
    if blockchain_proc.status() != "running":
        return jsonify({"message": "ブロックチェーンサーバーが起動していません"}), 409
    try:
        res = call_blockchain("/mine", timeout=60)
    except requests.exceptions.RequestException as e:
        return jsonify({"message": f"マイニングに失敗しました: {e}"}), 502
    if res.status_code == 200:
        return jsonify({"message": "マイニングしました(ブロックを1つ追加)"}), 200
    return jsonify({"message": "取引がないためマイニングしませんでした"}), 200


@app.route("/api/mine/start", methods=["POST"])
def api_mine_start():
    if blockchain_proc.status() != "running":
        return jsonify({"message": "ブロックチェーンサーバーが起動していません"}), 409
    try:
        call_blockchain("/mine/start", timeout=60)
    except requests.exceptions.RequestException as e:
        return jsonify({"message": f"自動マイニングを開始できませんでした: {e}"}), 502
    auto_mining["enabled"] = True
    return jsonify(
        {"message": "自動マイニングを開始しました(一定間隔でマイニング)"}
    ), 200


@app.route("/api/users", methods=["POST"])
def api_add_user():
    data = request.get_json(silent=True) or {}
    port = data.get("port")
    if port in ("", None):
        port = None
    else:
        try:
            port = int(port)
        except (TypeError, ValueError):
            return jsonify({"message": "ポート番号は数字で指定してください"}), 400
    user, error = add_user(data.get("name"), port)
    if error:
        return jsonify({"message": error}), 409
    return jsonify(
        {
            "message": f"{user.name}(ポート{user.port})を追加しました",
            "user": user.to_dict(),
        }
    ), 201


@app.route("/api/users/<int:port>", methods=["DELETE"])
def api_delete_user(port):
    with lock:
        user = users.pop(port, None)
        if user is None:
            return jsonify({"message": "ユーザが見つかりません"}), 404
        user.stop()
    return jsonify({"message": f"{user.name}(ポート{port})を削除しました"}), 200


@app.route("/api/users/<int:port>/restart", methods=["POST"])
def api_restart_user(port):
    with lock:
        user = users.get(port)
        if user is None:
            return jsonify({"message": "ユーザが見つかりません"}), 404
        user.stop()
        user.start()
    return jsonify({"message": f"{user.name}を再起動しました"}), 200


@app.route("/api/logs/<key>", methods=["GET"])
def api_logs(key):
    with lock:
        if key == "blockchain":
            target = blockchain_proc
        else:
            target = next((u for u in users.values() if u.key == key), None)
    if target is None:
        return jsonify({"message": "ログが見つかりません"}), 404
    lines = min(max(request.args.get("lines", 200, type=int), 1), 1000)
    hide_polling = request.args.get("hide_polling") == "1"
    return jsonify(
        {"key": key, "log": read_tail(target.log_path, lines, hide_polling)}
    ), 200


def main():
    from argparse import ArgumentParser

    parser = ArgumentParser(description="ブロックチェーン学習用 管理ページ")
    parser.add_argument(
        "-p", "--port", default=9000, type=int, help="管理ページのポート"
    )
    parser.add_argument(
        "-u", "--users", default=2, type=int, help="最初に起動するユーザ数"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="ブラウザを自動で開かない"
    )
    args = parser.parse_args()
    app.config["admin_port"] = args.port

    if is_port_in_use(args.port):
        print(
            f"ポート{args.port}は使用中です。管理ページが既に起動していないか確認してください。"
        )
        sys.exit(1)

    atexit.register(stop_all)
    if not IS_WINDOWS:
        # ターミナルを閉じたときなども、起動したサーバーを停止してから終了する
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, lambda *_: sys.exit(0))

    error = start_blockchain()
    if error:
        print(error)
    for _ in range(max(0, args.users)):
        _, error = add_user()
        if error:
            print(error)
            break

    url = f"http://localhost:{args.port}/"
    print("=" * 60)
    print(f" 管理ページ: {url}")
    print(" 終了するときはこの画面で Ctrl + C を押してください")
    print("=" * 60)
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=[url]).start()

    # 管理ページ自体は自動リロードを使わない(サーバーが二重に起動するのを防ぐ)
    # 外部から操作されないよう、自分のPC(127.0.0.1)からのみアクセス可能にする
    app.run(
        host="127.0.0.1", port=args.port, threaded=True, debug=False, use_reloader=False
    )


if __name__ == "__main__":
    main()
