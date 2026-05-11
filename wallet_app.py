from flask import Flask, render_template_string, request, redirect, url_for, flash, Response
import sqlite3
import secrets
import time
import io
import qrcode
from pathlib import Path

APP_NAME = "Bitcoin Wallet Trainer"
DB_PATH = Path("wallet_trainer.db")

app = Flask(__name__)
app.secret_key = "dev-secret-key"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fake_address():
    return "bc1qsim" + secrets.token_hex(18)


def fake_txid():
    return secrets.token_hex(32)


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS transactions")
    cur.execute("DROP TABLE IF EXISTS wallets")

    cur.execute("""
        CREATE TABLE wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            address TEXT NOT NULL UNIQUE,
            balance INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txid TEXT NOT NULL UNIQUE,
            sender_wallet_id INTEGER NOT NULL,
            receiver_wallet_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            fee INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            confirmations INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending confirmation'
        )
    """)

    cur.execute(
        "INSERT INTO wallets (name, address, balance) VALUES (?, ?, ?)",
        ("Erica", fake_address(), 100000),
    )
    cur.execute(
        "INSERT INTO wallets (name, address, balance) VALUES (?, ?, ?)",
        ("Neil", fake_address(), 100000),
    )

    conn.commit()
    conn.close()


def refresh_confirmations():
    now = int(time.time())
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute("SELECT id, created_at FROM transactions").fetchall()

    for row in rows:
        elapsed = now - row["created_at"]
        confirmations = min(elapsed // 10, 6)
        status = "confirmed" if confirmations >= 1 else "pending confirmation"
        cur.execute(
            "UPDATE transactions SET confirmations = ?, status = ? WHERE id = ?",
            (confirmations, status, row["id"]),
        )

    conn.commit()
    conn.close()


def load_page(sent_tx=None):
    refresh_confirmations()
    conn = get_db()
    erica = conn.execute("SELECT * FROM wallets WHERE name = 'Erica'").fetchone()
    neil = conn.execute("SELECT * FROM wallets WHERE name = 'Neil'").fetchone()
    transactions = conn.execute("""
        SELECT
            t.*,
            s.name AS sender_name,
            r.name AS receiver_name
        FROM transactions t
        JOIN wallets s ON s.id = t.sender_wallet_id
        JOIN wallets r ON r.id = t.receiver_wallet_id
        ORDER BY t.created_at DESC
        LIMIT 20
    """).fetchall()

    # Latest incoming tx for each wallet, plus the newest tx overall.
    latest_tx = None
    latest_neil_tx = None
    latest_display_tx = transactions[0] if transactions else None

    for tx in transactions:
        if tx["receiver_name"] == "Erica" and latest_tx is None:
            latest_tx = tx
        if tx["receiver_name"] == "Neil" and latest_neil_tx is None:
            latest_neil_tx = tx

    conn.close()

    return render_template_string(
        TEMPLATE,
        app_name=APP_NAME,
        erica=erica,
        neil=neil,
        transactions=transactions,
        sent_tx=sent_tx,
        latest_tx=latest_tx,
        latest_neil_tx=latest_neil_tx,
        latest_display_tx=latest_display_tx,
    )



def ensure_db_ready():
    try:
        conn = get_db()
        conn.execute("SELECT 1 FROM transactions LIMIT 1")
        conn.close()
    except sqlite3.OperationalError:
        init_db()


@app.before_request
def before_request():
    ensure_db_ready()



@app.route("/")
def index():
    return load_page(sent_tx=None)


@app.route("/qr/erica")
def qr_erica():
    conn = get_db()
    erica = conn.execute("SELECT address FROM wallets WHERE name = 'Erica'").fetchone()
    conn.close()

    img = qrcode.make(erica["address"])
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return Response(buffer.getvalue(), mimetype="image/png")


@app.route("/qr/neil")
def qr_neil():
    conn = get_db()
    neil = conn.execute("SELECT address FROM wallets WHERE name = 'Neil'").fetchone()
    conn.close()

    img = qrcode.make(neil["address"])
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return Response(buffer.getvalue(), mimetype="image/png")


@app.route("/send", methods=["POST"])
def send():
    receiver_address = request.form.get("receiver_address", "").strip()
    amount = request.form.get("amount", type=int)
    fee = 250

    if not receiver_address or not amount:
        flash("Paste Erica's receive address and enter an amount.", "error")
        return redirect(url_for("index"))

    if amount <= 0:
        flash("Amount must be greater than zero.", "error")
        return redirect(url_for("index"))

    conn = get_db()
    cur = conn.cursor()

    sender = cur.execute("SELECT * FROM wallets WHERE name = 'Neil'").fetchone()
    receiver = cur.execute("SELECT * FROM wallets WHERE address = ?", (receiver_address,)).fetchone()

    if not receiver:
        conn.close()
        flash("That receive address was not found. Click Receive on Erica's wallet and copy that address.", "error")
        return redirect(url_for("index"))

    if sender["id"] == receiver["id"]:
        conn.close()
        flash("Neil cannot send to himself in this beginner lesson.", "error")
        return redirect(url_for("index"))

    total_cost = amount + fee
    if sender["balance"] < total_cost:
        conn.close()
        flash(f"Neil needs {total_cost:,} sats including the {fee:,} sat training fee.", "error")
        return redirect(url_for("index"))

    txid = fake_txid()
    created_at = int(time.time())

    cur.execute("UPDATE wallets SET balance = balance - ? WHERE id = ?", (total_cost, sender["id"]))
    cur.execute("UPDATE wallets SET balance = balance + ? WHERE id = ?", (amount, receiver["id"]))
    cur.execute("""
        INSERT INTO transactions (
            txid, sender_wallet_id, receiver_wallet_id, amount, fee, created_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (txid, sender["id"], receiver["id"], amount, fee, created_at, "pending confirmation"))

    conn.commit()
    conn.close()

    return redirect(url_for("sent", txid=txid))


@app.route("/tx_status/<txid>")
def tx_status(txid):
    refresh_confirmations()
    conn = get_db()
    tx = conn.execute(
        "SELECT confirmations, status FROM transactions WHERE txid = ?",
        (txid,)
    ).fetchone()
    conn.close()

    if not tx:
        return {"error": "not found"}

    return {
        "confirmations": tx["confirmations"],
        "status": tx["status"]
    }


@app.route("/sent/<txid>")
def sent(txid):
    conn = get_db()
    tx = conn.execute("""
        SELECT
            t.*,
            s.name AS sender_name,
            r.name AS receiver_name
        FROM transactions t
        JOIN wallets s ON s.id = t.sender_wallet_id
        JOIN wallets r ON r.id = t.receiver_wallet_id
        WHERE t.txid = ?
    """, (txid,)).fetchone()
    conn.close()
    return load_page(sent_tx=tx)


@app.route("/send_back", methods=["POST"])
def send_back():
    receiver_address = request.form.get("receiver_address", "").strip()
    amount = request.form.get("amount", type=int)
    fee = 250

    if not receiver_address:
        flash("Paste Neil's receive address before sending back.", "error")
        return redirect(url_for("index"))

    if not amount or amount <= 0:
        flash("Enter a valid amount to send back.", "error")
        return redirect(url_for("index"))

    conn = get_db()
    cur = conn.cursor()

    sender = cur.execute("SELECT * FROM wallets WHERE name = 'Erica'").fetchone()
    receiver = cur.execute("SELECT * FROM wallets WHERE address = ?", (receiver_address,)).fetchone()

    if not receiver:
        conn.close()
        flash("That receive address was not found. Click Receive on Neil's wallet and copy his address.", "error")
        return redirect(url_for("index"))

    if receiver["name"] != "Neil":
        conn.close()
        flash("For this send-back lesson, Erica needs Neil's receive address.", "error")
        return redirect(url_for("index"))

    total_cost = amount + fee
    if sender["balance"] < total_cost:
        conn.close()
        flash(f"Erica needs {total_cost:,} sats including the {fee:,} sat training fee.", "error")
        return redirect(url_for("index"))

    txid = fake_txid()
    created_at = int(time.time())

    cur.execute("UPDATE wallets SET balance = balance - ? WHERE id = ?", (total_cost, sender["id"]))
    cur.execute("UPDATE wallets SET balance = balance + ? WHERE id = ?", (amount, receiver["id"]))
    cur.execute("""
        INSERT INTO transactions (
            txid, sender_wallet_id, receiver_wallet_id, amount, fee, created_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (txid, sender["id"], receiver["id"], amount, fee, created_at, "pending confirmation"))

    conn.commit()
    conn.close()

    return redirect(url_for("sent", txid=txid))


@app.route("/reset", methods=["POST"])
def reset():
    init_db()
    flash("Training wallets reset. Erica and Neil each have 100,000 sats.", "success")
    return redirect(url_for("index"))


TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ app_name }}</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #1b1b1b, #3a2a10);
            color: #222;
        }
        .page {
            max-width: 980px;
            margin: 0 auto;
            padding: 22px 14px 40px;
        }
        .header {
            color: white;
            text-align: center;
            margin-bottom: 18px;
        }
        .header h1 { margin: 0; }
        .header p { color: #ddd; }
        .notice {
            background: #fff3cd;
            border: 1px solid #ffe69c;
            padding: 12px;
            border-radius: 14px;
            margin-bottom: 16px;
            text-align: center;
        }
        .guide-bar {
            position: sticky;
            top: 10px;
            z-index: 1000;
            background: #f7931a;
            color: #0f172a;
            padding: 12px;
            border-radius: 14px;
            margin: 0 0 14px;
            font-weight: bold;
            text-align: center;
            box-shadow: 0 3px 10px rgba(0,0,0,0.18);
            border: 1px solid #93c5fd;
        }
        .education-box {
            background: #eef6ff;
            border: 1px solid #bfdbfe;
            border-radius: 16px;
            padding: 14px 16px;
            margin: 0 0 18px;
            color: #111827;
            box-shadow: 0 3px 10px rgba(0,0,0,0.10);
        }
        .education-box summary {
            cursor: pointer;
            font-weight: bold;
            font-size: 18px;
            list-style-position: inside;
        }
        .education-box summary::-webkit-details-marker {
            margin-right: 6px;
        }
        .education-box h3 {
            margin: 0 0 8px;
            font-size: 18px;
        }
        .education-box p {
            margin: 6px 0;
            line-height: 1.4;
            font-size: 14px;
        }
        .education-box ul {
            margin: 8px 0 0 20px;
            padding: 0;
            font-size: 14px;
            line-height: 1.45;
        }
        .flash {
            padding: 12px;
            border-radius: 14px;
            margin-bottom: 10px;
        }
        .success { background: #d1e7dd; }
        .error { background: #f8d7da; }
        .phones {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
        }
        .phone {
            background: #f6f6f6;
            border: 8px solid #111;
            border-radius: 34px;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(0,0,0,0.35);
        }
        .topbar {
            background: #111;
            color: white;
            text-align: center;
            padding: 18px;
        }
        .topbar h2 { margin: 0; }
        .topbar p { color: #ccc; font-size: 13px; }
        .content { padding: 18px; }
        .wallet-card.highlight {
            animation: flashGlow 3s ease;
        }
        @keyframes flashGlow {
            0%   { box-shadow: 0 0 0px rgba(247,147,26,0); }
            25%  { box-shadow: 0 0 25px rgba(247,147,26,0.9); }
            50%  { box-shadow: 0 0 5px rgba(247,147,26,0.3); }
            75%  { box-shadow: 0 0 25px rgba(247,147,26,0.9); }
            100% { box-shadow: 0 0 0px rgba(247,147,26,0); }
        }
        .wallet-card {
            position: relative;
            border-radius: 24px;
            padding: 20px;
            color: #111;
            background: linear-gradient(160deg, #f7931a, #ffbd63);
        }
        .wallet-card.send {
            background: linear-gradient(160deg, #4da3ff, #9dccff);
        }
        .incoming {
            background: rgba(255,255,255,.85);
            padding: 10px;
            border-radius: 14px;
            margin-bottom: 12px;
            font-weight: bold;
        }
        .incoming span {
            font-size: 12px;
            font-weight: normal;
        }
        .sats-pop {
            position: absolute;
            right: 18px;
            top: 18px;
            background: #fff3cd;
            color: #111;
            font-size: 22px;
            font-weight: bold;
            padding: 10px 16px;
            border-radius: 16px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.25);
            z-index: 5;
            animation: satsFloat 2.4s ease forwards;
            pointer-events: none;
        }
        @keyframes satsFloat {
            0% { opacity: 0; transform: translateY(20px) scale(0.85); }
            20% { opacity: 1; transform: translateY(0) scale(1.05); }
            70% { opacity: 1; transform: translateY(-18px) scale(1); }
            100% { opacity: 0; transform: translateY(-38px) scale(0.95); }
        }
        .pulse-button {
            animation: pulseBtn 1.4s infinite;
        }
        @keyframes pulseBtn {
            0% { box-shadow: 0 0 0 0 rgba(247,147,26,0.75); }
            70% { box-shadow: 0 0 0 12px rgba(247,147,26,0); }
            100% { box-shadow: 0 0 0 0 rgba(247,147,26,0); }
        }
        .wallet-name {
            font-size: 26px;
            font-weight: bold;
            background: rgba(255,255,255,.75);
            padding: 12px;
            border-radius: 14px;
        }
        .label {
            font-size: 13px;
            margin-bottom: 8px;
            opacity: .75;
        }
        .balance-label {
            margin-top: 18px;
            font-size: 13px;
            opacity: .75;
        }
        .balance {
            font-size: 36px;
            font-weight: 800;
            margin-top: 4px;
        }
        .actions {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin: 18px 0;
        }
        button {
            border: none;
            border-radius: 16px;
            padding: 13px;
            font-size: 15px;
            font-weight: bold;
            cursor: pointer;
            background: #111;
            color: white;
        }
        .orange { background: #f7931a; color: #111; }
        .blue { background: #4da3ff; color: #111; }
        .light { background: #e7e7e7; color: #111; }
        .panel {
            background: white;
            border-radius: 22px;
            padding: 16px;
            margin-top: 14px;
        }
        .hidden { display: none; }
        .address-box {
            background: #f1f1f1;
            border-radius: 14px;
            padding: 12px;
            font-family: monospace;
            font-size: 12px;
            word-break: break-all;
            margin-bottom: 10px;
        }
        .qr-box {
            text-align: center;
            margin: 12px auto;
        }
        .qr-box img {
            width: 170px;
            height: 170px;
            background: white;
            padding: 10px;
            border-radius: 12px;
            box-shadow: 0 0 0 1px #ddd;
        }
        input {
            width: 100%;
            padding: 13px;
            font-size: 15px;
            border-radius: 14px;
            border: 1px solid #ddd;
        }
        label {
            display: block;
            margin: 12px 0 6px;
            font-weight: bold;
            font-size: 13px;
        }
        .row {
            display: flex;
            gap: 8px;
        }
        .row input { flex: 1; min-width: 0; }
        .activity {
            background: #f6f6f6;
            border-radius: 24px;
            padding: 18px;
            margin-top: 24px;
        }
        .tx-item {
            background: white;
            border-radius: 16px;
            padding: 12px;
            margin-bottom: 10px;
        }
        .tx-main {
            display: flex;
            justify-content: space-between;
            font-weight: bold;
            gap: 10px;
        }
        .tx-sub, .txid {
            color: #666;
            font-size: 12px;
            margin-top: 5px;
        }
        .txid {
            font-family: monospace;
            word-break: break-all;
        }
        .modal-backdrop {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.6);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .modal {
            background: white;
            padding: 24px;
            border-radius: 20px;
            max-width: 340px;
            text-align: center;
        }
        .modal .txid-small {
            font-size: 11px;
            word-break: break-all;
        }
        .modal a {
            display: inline-block;
            padding: 10px 20px;
            background: #4da3ff;
            color: #111;
            border-radius: 12px;
            text-decoration: none;
            font-weight: bold;
        }
        @media (max-width: 760px) {
            .phones { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
<div class="page">
    <div class="header">
        <h1>{{ app_name }}</h1>
        <p>Two-wallet practice mode</p>
    </div>

    <div class="notice"><strong>Training only:</strong> fake sats, fake addresses, fake transactions.</div>

    {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
            {% for category, message in messages %}
                <div class="flash {{ category }}">{{ message }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <details class="education-box" open>
        <summary>How Bitcoin Transactions Work</summary>
        <p>Sending Bitcoin is similar to mailing a letter: before you can send it, you need the receiver's address.</p>
        <p>In the real world, the receiver usually shares their receive address by text message, email, QR code, or messaging app.</p>
        <p>In this trainer, you have the wallet create a receive address, then paste that address into the other wallet, enter an amount, and sends sats.</p>
        <ul>
            <li>The receive address tells the wallet where to send the sats.</li>
            <li>The transaction starts as pending confirmation.</li>
            <li>Confirmations show the network has accepted the transaction.</li>
        </ul>
    </details>

    <div id="guide" class="guide-bar">
        {% if sent_tx %}
            Transaction sent. Click Done to view Erica's received sats.
        {% elif latest_tx %}
            ✅ Transaction complete!
        {% else %}
            Step 1: Tap Receive on Erica's wallet
        {% endif %}
    </div>

    <div class="phones">
        <div class="phone">
            <div class="topbar">
                <h2>Erica's Wallet</h2>
                <p></p>
            </div>
            <div class="content">
                <div class="wallet-card {% if latest_display_tx and latest_display_tx.receiver_name == 'Erica' %}highlight{% endif %}" id="ericaWallet">
                    {% if latest_display_tx and latest_display_tx.receiver_name == 'Erica' %}
                        <div class="incoming">
                            Incoming transaction: +{{ "{:,}".format(latest_display_tx.amount) }} sats<br>
                            <span id="txStatus">
                                {% if latest_display_tx.status == "confirmed" %}
                                    ✅ Confirmed · {{ latest_display_tx.confirmations }} conf.
                                {% else %}
                                    ⏳ Waiting for confirmation... · {{ latest_display_tx.confirmations }} conf.
                                {% endif %}
                            </span>
                        </div>
                    {% endif %}
                    <div class="label">Erica's Wallet</div>
                    <div class="wallet-name">Erica</div>
                    <div class="balance-label">Available balance</div>
                    <div class="balance">{{ "{:,}".format(erica.balance) }} sats</div>
                    {% if latest_display_tx and latest_display_tx.receiver_name == 'Erica' %}
                        <div id="satsPop" class="sats-pop hidden">+{{ "{:,}".format(latest_display_tx.amount) }} sats</div>
                    {% endif %}
                </div>

                <div class="actions">
                    <button class="orange" onclick="showEricaSendNote()">Send</button>
                    <button id="receiveBtn" class="orange" onclick="showReceive()">Receive</button>
                </div>

                <div class="panel hidden" id="ericaSendNote">
                    <h3>Send Back Practice</h3>
                    {% if latest_tx %}
                        <p>Step A: Neil generates a receive address. Step B: Erica pastes it here and sends sats back.</p>
                        <form method="post" action="{{ url_for('send_back') }}">
                            <label>Neil's receiving address</label>
                            <div class="row">
                                <input id="sendBackAddress" name="receiver_address" placeholder="Paste Neil's receive address" required>
                                <button type="button" class="light" onclick="pasteNeilAddress()">Paste</button>
                            </div>
                            <label>Amount in sats</label>
                            <input id="sendBackAmount" name="amount" type="number" min="1" value="{{ latest_tx.amount }}" required>
                            <button class="orange" style="width:100%; margin-top:14px;" type="submit">Send Back to Neil</button>
                        </form>
                    {% else %}
                        <p>For this beginner lesson, Erica receives first. After Neil sends sats to Erica, this button will let Erica send sats back.</p>
                    {% endif %}
                </div>

                <div class="panel hidden" id="receivePanel">
                    <h3>Receive Sats</h3>
                    <p>Copy Erica's address to share with Neil (QR code shown for realism).</p>
                    <div class="address-box" id="ericaAddress">{{ erica.address }}</div>
                    <button id="copyBtn" class="orange" style="width:100%; margin-bottom:12px;" onclick="copyAddress()">Copy Receive Address</button>
                    <div class="qr-box">
                        <img src="{{ url_for('qr_erica') }}" alt="QR code for Erica's receive address">
                    </div>
                </div>
            </div>
        </div>

        <div class="phone">
            <div class="topbar">
                <h2>Neil's Wallet</h2>
                <p></p>
            </div>
            <div class="content">
                <div class="wallet-card send" id="neilWallet">
                    {% if latest_display_tx and latest_display_tx.receiver_name == 'Neil' %}
                        <div class="incoming">
                            Incoming transaction: +{{ "{:,}".format(latest_display_tx.amount) }} sats<br>
                            <span id="txStatus">
                                {% if latest_display_tx.status == "confirmed" %}
                                    ✅ Confirmed · {{ latest_display_tx.confirmations }} conf.
                                {% else %}
                                    ⏳ Waiting for confirmation... · {{ latest_display_tx.confirmations }} conf.
                                {% endif %}
                            </span>
                        </div>
                    {% endif %}
                    <div class="label">Neil's Wallet</div>
                    <div class="wallet-name">Neil</div>
                    <div class="balance-label">Available balance</div>
                    <div class="balance">{{ "{:,}".format(neil.balance) }} sats</div>
                    {% if latest_display_tx and latest_display_tx.receiver_name == 'Neil' %}
                        <div id="satsPop" class="sats-pop hidden">+{{ "{:,}".format(latest_display_tx.amount) }} sats</div>
                    {% endif %}
                </div>

                <div class="actions">
                    <button id="sendBtn" class="blue" onclick="showSend()">Send</button>
                    <button class="blue" onclick="showNeilReceiveNote()">Receive</button>
                </div>

                <div class="panel hidden" id="neilReceiveNote">
                    <h3>Receive Sats</h3>
                    <p>Copy Neil's address so Erica can send sats back.</p>
                    <div class="address-box" id="neilAddress">{{ neil.address }}</div>
                    <button class="blue" style="width:100%; margin-bottom:12px;" onclick="copyNeilAddress()">Copy Neil's Receive Address</button>
                    <div class="qr-box">
                        <img src="{{ url_for('qr_neil') }}" alt="QR code for Neil's receive address">
                    </div>
                </div>

                <div class="panel hidden" id="sendPanel">
                    <h3>Send Sats</h3>
                    <form method="post" action="{{ url_for('send') }}">
                        <label>Receiving address</label>
                        <div class="row">
                            <input id="receiver_address" name="receiver_address" placeholder="Paste Erica's address" required>
                            <button id="pasteBtn" type="button" class="light" onclick="pasteAddress()">Paste</button>
                        </div>

                        <label>Amount in sats</label>
                        <input id="amountInput" name="amount" type="number" min="1" placeholder="Example: 5000" required>

                        <button class="blue" style="width:100%; margin-top:14px;" type="submit">
                            Send Fake Transaction
                        </button>
                    </form>
                </div>
            </div>
        </div>
    </div>

    <div class="activity">
        <h2>Recent Activity</h2>
        {% for tx in transactions %}
            <div class="tx-item">
                <div class="tx-main">
                    <span>{{ tx.sender_name }} → {{ tx.receiver_name }}</span>
                    <span>{{ "{:,}".format(tx.amount) }} sats</span>
                </div>
                <div class="tx-sub" id="activityStatus-{{ tx.txid }}">Fee: {{ "{:,}".format(tx.fee) }} sats · {{ tx.status }} · {{ tx.confirmations }} conf.</div>
                <div class="txid">{{ tx.txid }}</div>
            </div>
        {% else %}
            <p>No transactions yet.</p>
        {% endfor %}

        <form method="post" action="{{ url_for('reset') }}">
            <button class="light" style="width:100%;">Reset Training Wallets</button>
        </form>
    </div>
</div>



{% if sent_tx %}
<div class="modal-backdrop">
    <div class="modal">
        <h2>Transaction Sent</h2>
        <p><strong>{{ "{:,}".format(sent_tx.amount) }} sats</strong></p>
        <p>From {{ sent_tx.sender_name }} to {{ sent_tx.receiver_name }}</p>
        <p>Fee: {{ "{:,}".format(sent_tx.fee) }} sats</p>
        <p class="txid-small">TXID: {{ sent_tx.txid }}</p>
        <p>Status: Pending confirmation</p>
        <br>
        <a href="/">Done</a>
    </div>
</div>
{% endif %}

<script>
const ericaAddress = "{{ erica.address }}";
const latestTxId = "{{ latest_display_tx.txid if latest_display_tx else '' }}";
const latestTxFee = "{{ latest_display_tx.fee if latest_display_tx else '' }}";
const sentTxActive = "{{ 'true' if sent_tx else 'false' }}" === "true";
const latestReceiver = "{{ latest_display_tx.receiver_name if latest_display_tx else '' }}";

function advanceGuide(stepText) {
    const guide = document.getElementById('guide');
    if (guide) guide.innerText = stepText;
}

function clearButtonHighlights() {
    const receive = document.getElementById('receiveBtn');
    const copy = document.getElementById('copyBtn');
    const send = document.getElementById('sendBtn');
    const paste = document.getElementById('pasteBtn');
    const amount = document.getElementById('amountInput');

    if (receive) receive.classList.remove('pulse-button');
    if (copy) copy.classList.remove('pulse-button');
    if (send) send.classList.remove('pulse-button');
    if (paste) paste.classList.remove('pulse-button');
    if (amount) amount.classList.remove('pulse-button');
}

function highlightReceive() {
    clearButtonHighlights();
    const receive = document.getElementById('receiveBtn');
    if (receive) receive.classList.add('pulse-button');
}

function highlightCopy() {
    clearButtonHighlights();
    const copy = document.getElementById('copyBtn');
    if (copy) copy.classList.add('pulse-button');
}

function highlightSend() {
    clearButtonHighlights();
    const send = document.getElementById('sendBtn');
    if (send) send.classList.add('pulse-button');
}

function highlightPaste() {
    clearButtonHighlights();
    const paste = document.getElementById('pasteBtn');
    if (paste) paste.classList.add('pulse-button');
}

function highlightAmount() {
    clearButtonHighlights();
    const amount = document.getElementById('amountInput');
    if (amount) amount.classList.add('pulse-button');
}

function hideEricaPanels() {
    const sendNote = document.getElementById('ericaSendNote');
    const receivePanel = document.getElementById('receivePanel');
    if (sendNote) sendNote.classList.add('hidden');
    if (receivePanel) receivePanel.classList.add('hidden');
}

function hideNeilPanels() {
    const receiveNote = document.getElementById('neilReceiveNote');
    const sendPanel = document.getElementById('sendPanel');
    if (receiveNote) receiveNote.classList.add('hidden');
    if (sendPanel) sendPanel.classList.add('hidden');
}

function showReceive() {
    hideEricaPanels();
    const receivePanel = document.getElementById('receivePanel');
    if (receivePanel) receivePanel.classList.remove('hidden');
    advanceGuide("Step 2: Copy Erica's address to share with Neil");
    highlightCopy();
}

function showEricaSendNote() {
    hideEricaPanels();
    const note = document.getElementById('ericaSendNote');
    if (note) note.classList.remove('hidden');
}

function showSend() {
    hideNeilPanels();
    const sendPanel = document.getElementById('sendPanel');
    if (sendPanel) sendPanel.classList.remove('hidden');
    advanceGuide("Step 3: Neil pastes Erica's shared address");
    highlightPaste();
}

function showNeilReceiveNote() {
    hideNeilPanels();
    const note = document.getElementById('neilReceiveNote');
    if (note) note.classList.remove('hidden');
    advanceGuide("Send-back: Copy Neil's receive address");
}

function copyNeilAddress() {
    showNeilReceiveNote();
    const neilAddress = "{{ neil.address }}";
    navigator.clipboard.writeText(neilAddress).then(() => {
        advanceGuide("Send-back: Click Erica's Send button and paste Neil's address");
        alert("Neil's receive address copied. Now paste it into Erica's send-back form.");
    }).catch(() => {
        advanceGuide("Send-back: Manually copy Neil's address, then paste it into Erica's send-back form");
        alert("Copy failed. You can manually select and copy Neil's address.");
    });
}

function pasteNeilAddress() {
    navigator.clipboard.readText().then(text => {
        const input = document.getElementById('sendBackAddress');
        if (input) input.value = text;
        advanceGuide("Send-back: Enter an amount, then click Send Back to Neil");
    }).catch(() => {
        advanceGuide("Send-back: Paste Neil's address manually, enter an amount, then send back");
    });
}

function copyAddress() {
    showReceive();
    navigator.clipboard.writeText(ericaAddress).then(() => {
        advanceGuide("Step 3: Click Send on Neil's wallet");
        highlightSend();
        alert("Erica's receive address copied. Now paste it into Neil's wallet.");
    }).catch(() => {
        advanceGuide("Step 3: Click Send on Neil's wallet");
        highlightSend();
        alert("Copy failed. You can manually select and copy Erica's address.");
    });
}

function pasteAddress() {
    showSend();
    navigator.clipboard.readText().then(text => {
        document.getElementById('receiver_address').value = text;
        advanceGuide("Step 3: Enter an amount, then click Send Fake Transaction");
        highlightAmount();
    }).catch(() => {
        advanceGuide("Step 3: Paste Erica's address manually, enter an amount, then send");
        highlightAmount();
    });
}

function simulateWrongAddress() {
    showSend();
    document.getElementById('receiver_address').value = 'bc1qwrongtrainingaddress000000000000000';
    document.getElementById('amountInput').value = '5000';
    advanceGuide('Mistake simulation: click Send Fake Transaction to see a wrong-address error');
    clearButtonHighlights();
}

function simulateTooMuch() {
    showSend();
    document.getElementById('receiver_address').value = ericaAddress;
    document.getElementById('amountInput').value = '999999999';
    advanceGuide('Mistake simulation: click Send Fake Transaction to see an insufficient-funds error');
    clearButtonHighlights();
}

function triggerReceiverHighlight() {
    const walletId = latestReceiver === 'Neil' ? 'neilWallet' : 'ericaWallet';
    const wallet = document.getElementById(walletId);
    if (!wallet) return;
    wallet.classList.remove('highlight');
    void wallet.offsetWidth;
    wallet.classList.add('highlight');
}

function showSatsPop() {
    const pop = document.getElementById('satsPop');
    if (!pop) return;
    clearButtonHighlights();
    pop.classList.remove('hidden');
}

function playReceiveSound() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;

    const audioContext = new AudioContextClass();
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();

    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(880, audioContext.currentTime);
    oscillator.frequency.setValueAtTime(1320, audioContext.currentTime + 0.12);

    gain.gain.setValueAtTime(0.001, audioContext.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.08, audioContext.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + 0.45);

    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start();
    oscillator.stop(audioContext.currentTime + 0.5);
}

function watchConfirmation() {
    if (!latestTxId) return;

    const statusEl = document.getElementById('txStatus');
    if (!statusEl) return;

    let lastStatus = '';
    let lastConfirmations = -1;

    setInterval(() => {
        fetch(`/tx_status/${latestTxId}`)
            .then(response => response.json())
            .then(data => {
                if (!data || data.error) return;

                const statusChanged = data.status !== lastStatus;
                const confirmationsChanged = data.confirmations !== lastConfirmations;

                lastStatus = data.status;
                lastConfirmations = data.confirmations;

                if (data.status === 'confirmed') {
                    statusEl.innerHTML = `✅ Confirmed · ${data.confirmations} conf.`;
                    statusEl.style.color = 'green';
                } else {
                    statusEl.innerHTML = `⏳ Waiting for confirmation... · ${data.confirmations} conf.`;
                    statusEl.style.color = '';
                }

                const activityEl = document.getElementById(`activityStatus-${latestTxId}`);
                if (activityEl) {
                    activityEl.innerHTML = `Fee: ${Number(latestTxFee).toLocaleString()} sats · ${data.status} · ${data.confirmations} conf.`;
                    if (data.status === 'confirmed') activityEl.style.color = 'green';
                }

                if (statusChanged || confirmationsChanged) triggerReceiverHighlight();
            })
            .catch(() => {});
    }, 3000);
}

window.addEventListener('load', () => {
    // Only show Step 1 if truly idle (no active or just-sent transaction)
    if (!latestTxId && !sentTxActive) {
        advanceGuide("Step 1: Tap Receive on Erica's wallet");
        highlightReceive();
    }
});

{% if latest_tx and not sent_tx %}
setTimeout(() => {
    advanceGuide('✅ Transaction complete!');
    triggerReceiverHighlight();
    showSatsPop();
    playReceiveSound();
    watchConfirmation();
}, 800);
{% endif %}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
