"""
AさんからBさんへの送金をテストするスクリプト
事前に blockchain_server.py(5000) と wallet_server.py(8080) を起動しておくこと
"""

import requests

WALLET_SERVER = "http://127.0.0.1:8080"
BLOCKCHAIN_SERVER = "http://127.0.0.1:5000"

# AさんとBさんのウォレットを作成
wallet_a = requests.post(f"{WALLET_SERVER}/wallet", timeout=3).json()
wallet_b = requests.post(f"{WALLET_SERVER}/wallet", timeout=3).json()
print("Aさんのアドレス:", wallet_a["blockchain_address"])
print("Bさんのアドレス:", wallet_b["blockchain_address"])

# AさんからBさんへ 1.5 送金(署名はウォレットサーバー側で作成される)
res = requests.post(
    f"{WALLET_SERVER}/wallet/transaction",
    json={
        "sender_private_key": wallet_a["private_key"],
        "sender_public_key": wallet_a["public_key"],
        "sender_blockchain_address": wallet_a["blockchain_address"],
        "recipient_blockchain_address": wallet_b["blockchain_address"],
        "value": 1.5,
    },
    timeout=5,
)
print("送金結果:", res.status_code, res.json())

# ブロックチェーンサーバーのトランザクションプールを確認
print(
    "/transaction の中身:",
    requests.get(f"{BLOCKCHAIN_SERVER}/transaction", timeout=3).json(),
)
