import urllib.parse
from flask import Flask, jsonify, render_template, request
import requests

import wallet

app = Flask(__name__, template_folder="./templates")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/wallet", methods=["GET", "POST"])
def create_wallet():
    my_wallet = wallet.Wallet()
    response = {
        "private_key": my_wallet.private_key,
        "public_key": my_wallet.public_key,
        "blockchain_address": my_wallet.blockchain_address,
    }
    return jsonify(response), 200


@app.route("/wallet/transaction", methods=["POST"])
def create_transaction():
    request_json = request.get_json(silent=True)
    if request_json is None:
        return jsonify({"message": "Invalid JSON"}), 400
    required = (
        "sender_private_key",
        "sender_public_key",
        "sender_blockchain_address",
        "recipient_blockchain_address",
        "value",
    )
    if not all(k in request_json for k in required):
        return jsonify({"message": "Missing values"}), 400

    sender_private_key = request_json["sender_private_key"]
    sender_blockchain_address = request_json["sender_blockchain_address"]
    recipient_blockchain_address = request_json["recipient_blockchain_address"]
    sender_public_key = request_json["sender_public_key"]
    try:
        value = float(request_json["value"])
    except (TypeError, ValueError):
        return jsonify({"message": "Invalid value"}), 400

    transaction = wallet.Transaction(
        sender_private_key,
        sender_public_key,
        sender_blockchain_address,
        recipient_blockchain_address,
        value,
    )
    json_data = {
        "sender_blockchain_address": sender_blockchain_address,
        "recipient_blockchain_address": recipient_blockchain_address,
        "value": value,
        "sender_public_key": sender_public_key,
        "signature": transaction.generate_signature(),
    }

    url = urllib.parse.urljoin(app.config["gw"], "transaction")
    try:
        # PCのプロキシ設定(環境変数やWindowsのインターネットオプション)を無視して、
        # ローカルのブロックチェーンサーバーへ直接接続する
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(url, json=json_data, timeout=10)
    except requests.exceptions.RequestException as e:
        app.logger.error({"action": "create_transaction", "url": url, "error": str(e)})
        return jsonify(
            {
                "message": "fail",
                "error": "blockchain server unreachable",
                "url": url,
                "detail": str(e),
            }
        ), 502

    if response.status_code == 201:
        return jsonify({"message": "success"}), 201

    # Response オブジェクトはそのままJSON化できないので、本文を返す
    return jsonify({"message": "fail", "response": response.text}), 400


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser()
    parser.add_argument(
        "-p", "--port", default=8080, type=int, help="port to listen on"
    )
    parser.add_argument(
        "-g",
        "--gw",
        default="http://127.0.0.1:5000",
        type=str,
        help="blockchain gateway",
    )
    args = parser.parse_args()
    port = args.port
    app.config["port"] = port
    app.config["gw"] = args.gw

    app.run(host="0.0.0.0", port=port, threaded=True, debug=True)
