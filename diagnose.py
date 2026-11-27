"""
送金がタイムアウトする原因を切り分ける診断スクリプト
blockchain_server.py(5000) と wallet_server.py(8080) を起動した状態で実行する
"""

import time

import requests

import wallet

BLOCKCHAIN_SERVER = "http://127.0.0.1:5000"
WALLET_SERVER = "http://127.0.0.1:8080"

# プロキシ設定の影響を受けないように直接接続する
session = requests.Session()
session.trust_env = False


def check(title, method, url, **kwargs):
    start = time.time()
    try:
        res = session.request(method, url, **kwargs)
        print(
            f"[OK ] {title}: {res.status_code} {res.text.strip()[:120]} ({time.time() - start:.2f}秒)"
        )
        return res
    except requests.exceptions.RequestException as e:
        print(f"[NG ] {title}: {type(e).__name__} ({time.time() - start:.2f}秒)")
        return None


print("=== 1. ブロックチェーンサーバー(5000)へ GET")
r1 = check("GET /transaction", "GET", f"{BLOCKCHAIN_SERVER}/transaction", timeout=5)

print("=== 2. ブロックチェーンサーバー(5000)へ 署名付き送金を直接 POST")
wallet_a = wallet.Wallet()
wallet_b = wallet.Wallet()
t = wallet.Transaction(
    wallet_a.private_key,
    wallet_a.public_key,
    wallet_a.blockchain_address,
    wallet_b.blockchain_address,
    1.0,
)
r2 = check(
    "POST /transaction",
    "POST",
    f"{BLOCKCHAIN_SERVER}/transaction",
    json={
        "sender_blockchain_address": wallet_a.blockchain_address,
        "recipient_blockchain_address": wallet_b.blockchain_address,
        "value": 1.0,
        "sender_public_key": wallet_a.public_key,
        "signature": t.generate_signature(),
    },
    timeout=10,
)

print("=== 3. ウォレットサーバー(8080)へ ウォレット作成")
r3 = check("POST /wallet", "POST", f"{WALLET_SERVER}/wallet", timeout=5)

print("=== 4. ウォレットサーバー(8080)経由で送金")
r4 = None
if r3 is not None and r3.status_code == 200:
    a = r3.json()
    r4 = check(
        "POST /wallet/transaction",
        "POST",
        f"{WALLET_SERVER}/wallet/transaction",
        json={
            "sender_private_key": a["private_key"],
            "sender_public_key": a["public_key"],
            "sender_blockchain_address": a["blockchain_address"],
            "recipient_blockchain_address": wallet_b.blockchain_address,
            "value": 1.0,
        },
        timeout=15,
    )

print("=== 判定")
if r1 is None or r2 is None:
    print("ブロックチェーンサーバー(5000)が応答していません。")
    print(
        "→ 古いサーバーが残っていないか、サーバーの黒い画面が「選択」状態で止まっていないか確認してください。"
    )
elif r2.status_code != 201:
    print(
        "ブロックチェーンサーバーは応答しましたが送金が拒否されました。上の内容を確認してください。"
    )
elif r3 is None or r4 is None:
    print("ウォレットサーバー(8080)が応答していません。")
    print(
        "→ 古いサーバーが残っていないか、サーバーの黒い画面が「選択」状態で止まっていないか確認してください。"
    )
elif r4.status_code != 201:
    print(
        "ウォレットサーバーからブロックチェーンサーバーへの転送に失敗しました。上の detail を確認してください。"
    )
else:
    print("すべて正常です。 /transaction にデータが入っています。")
