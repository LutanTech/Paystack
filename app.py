import os
import hmac
import hashlib
import json
from datetime import datetime

from flask import Flask, render_template, request, jsonify, abort, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import requests
from flask_cors import CORS
from flask_migrate import Migrate

# load .env
load_dotenv()

# config
PAYSTACK_PUBLIC = os.getenv("PAYSTACK_PUBLIC")
PAYSTACK_SECRET = os.getenv("PAYSTACK_SECRET")
PAYSTACK_WEBHOOK_SECRET = os.getenv("PAYSTACK_WEBHOOK_SECRET")
BASE_URL = os.getenv("BASE_URL", "") 

PAYSTACK_INIT_URL = "https://api.paystack.co/transaction/initialize"
PAYSTACK_VERIFY_URL = "https://api.paystack.co/transaction/verify/{}"

# Flask app + DB
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///transactions.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

Migrate(app)
CORS(app)

HEADERS = {
    "Authorization": f"Bearer {PAYSTACK_SECRET}",
    "Content-Type": "application/json",
}

# --- Models ---
class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(120), unique=True, index=True, nullable=False)
    access_code = db.Column(db.String(120))
    email = db.Column(db.String(200))
    amount = db.Column(db.Float)          
    currency = db.Column(db.String(10))
    status = db.Column(db.String(40), default="pending") 
    channel = db.Column(db.String(80), nullable=True)
    raw_response = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "reference": self.reference,
            "email": self.email,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
            "channel": self.channel,
            "created_at": self.created_at.isoformat(),
        }

with app.app_context():
    db.create_all()

# --- Routes ---

@app.route("/")
def index():
    return jsonify({'public_key':PAYSTACK_PUBLIC})

@app.route("/pay/initiate", methods=["POST"])
def initiate_payment():
    payload = request.get_json() or {}
    email = payload.get("email")
    amount = payload.get("amount")

    if not email or not amount:
        return jsonify({"status": False, "message": "email and amount required"}), 400

    # validate amount
    try:
        amount_value = float(amount)
        if amount_value <= 0:
            raise ValueError("amount must be > 0")
    except Exception as exc:
        return jsonify({"status": False, "message": "invalid amount", "error": str(exc)}), 400

    amount_kobo = int(round(amount_value * 100))

    data = {
        "email": email,
        "amount": amount_kobo,
        "currency": "KES",
        "metadata": {"integration": "lutan-pay"},
    }

    try:
        resp = requests.post(PAYSTACK_INIT_URL, headers=HEADERS, json=data, timeout=15)
        result = resp.json()
    except requests.RequestException as e:
        return jsonify({"status": False, "message": "network error", "error": str(e)}), 500

    if result.get("status") and result.get("data"):
        ref = result["data"].get("reference")
        access_code = result["data"].get("access_code")
        try:
            trx = Transaction(
                reference=ref,
                access_code=access_code,
                email=email,
                amount=amount_value,
                currency="KES",
                status="pending",
                raw_response=json.dumps(result)
            )
            db.session.add(trx)
            db.session.commit()
        except Exception as e:
            app.logger.error("DB save error: %s", e)

    return jsonify(result), resp.status_code if 'resp' in locals() else 500

@app.route("/pay/verify/<reference>", methods=["GET"])
def verify_payment(reference):
    url = PAYSTACK_VERIFY_URL.format(reference)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        result = resp.json()
    except requests.RequestException as e:
        return jsonify({"status": False, "message": "network error", "error": str(e)}), 500

    if result.get("status") and result.get("data"):
        data = result["data"]
        status = data.get("status")
        channel = data.get("channel")
        trx = Transaction.query.filter_by(reference=reference).first()
        if trx:
            trx.status = status
            trx.channel = channel
            trx.raw_response = json.dumps(result)
            db.session.commit()
    return jsonify(result), resp.status_code

@app.route("/pay/webhook", methods=["POST"])
def paystack_webhook():
    payload = request.get_data()
    signature = request.headers.get("x-paystack-signature")

    if PAYSTACK_WEBHOOK_SECRET:
        computed = hmac.new(PAYSTACK_WEBHOOK_SECRET.encode('utf-8'), payload, hashlib.sha512).hexdigest()
        if not signature or not hmac.compare_digest(computed, signature):
            app.logger.warning("Invalid webhook signature")
            return abort(400, "invalid signature")

    try:
        event = request.json
    except Exception:
        return abort(400, "invalid json")
    ev = event.get("event")
    data = event.get("data", {})

    if ev == "charge.success":
        reference = data.get("reference")
        status = data.get("status")
        channel = data.get("channel")
        trx = Transaction.query.filter_by(reference=reference).first()
        if trx:
            trx.status = status
            trx.channel = channel
            trx.raw_response = json.dumps(event)
            db.session.commit()
            app.logger.info("Marked transaction %s as %s", reference, status)
        else:
            try:
                t = Transaction(
                    reference=reference,
                    access_code=None,
                    email=data.get("customer", {}).get("email"),
                    amount=(data.get("amount") or 0) / 100.0,
                    currency=data.get("currency"),
                    status=status,
                    channel=channel,
                    raw_response=json.dumps(event)
                )
                db.session.add(t)
                db.session.commit()
            except Exception as e:
                app.logger.error("Failed to create trx from webhook: %s", e)

    return jsonify({"status": "ok"}), 200

@app.route("/admin/transactions")
def admin_transactions():
    rows = Transaction.query.order_by(Transaction.created_at.desc()).limit(200).all()
    return render_template("admin.html", transactions=rows)

@app.route("/admin/clear_pending", methods=["POST"])
def admin_clear_pending():
    try:
        num = Transaction.query.filter_by(status="pending").delete()
        db.session.commit()
        return jsonify({"status": True, "deleted": num})
    except Exception as e:
        return jsonify({"status": False, "error": str(e)}), 500

# run
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
