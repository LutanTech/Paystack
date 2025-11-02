import os
import hmac
import hashlib
import json
from datetime import datetime, timedelta
from datetime import datetime, timedelta

from flask import Flask, render_template, request, jsonify, abort, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import requests
from flask_cors import CORS
from flask_migrate import Migrate
import secrets, string
import json
import secrets, string
import json
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
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "connect_args": {
        "ssl": {"ssl_mode": "REQUIRED"}
    }
}

ALLOWED_FRONTEND_ORIGINS = [
     "https://pay.66ghz.com"
]

CORS(app, origins=ALLOWED_FRONTEND_ORIGINS, supports_credentials=True)

db = SQLAlchemy(app)

Migrate(app,db)

HEADERS = {
    "Authorization": f"Bearer {PAYSTACK_SECRET}",
    "Content-Type": "application/json",
}


def generate_random_id(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))



def generate_random_id(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


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
    created_at = db.Column(db.DateTime, default=lambda:datetime.utcnow() + timedelta(hours=3))
    updated_at = db.Column(db.DateTime, default=lambda:datetime.utcnow() + timedelta(hours=3), onupdate=lambda:datetime.utcnow() + timedelta(hours=3))
    created_at = db.Column(db.DateTime, default=lambda:datetime.utcnow() + timedelta(hours=3))
    updated_at = db.Column(db.DateTime, default=lambda:datetime.utcnow() + timedelta(hours=3), onupdate=lambda:datetime.utcnow() + timedelta(hours=3))

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
class Receipt(db.Model):
    id = db.Column(db.String(10), primary_key=True)
    content = db.Column(db.Text)
    at = db.Column(db.DateTime, default=lambda:datetime.utcnow() + timedelta(hours=3), onupdate=lambda:datetime.utcnow() + timedelta(hours=3))
    accessed = db.Column(db.Boolean,default=False)
    
    def to_dict(self):
        return{
            'id': self.id,
            "content":self.content,
            "at": self.at,
            'accessed':self.accessed
        }

class Receipt(db.Model):
    id = db.Column(db.String(10), primary_key=True)
    content = db.Column(db.Text)
    at = db.Column(db.DateTime, default=lambda:datetime.utcnow() + timedelta(hours=3), onupdate=lambda:datetime.utcnow() + timedelta(hours=3))
    accessed = db.Column(db.Boolean,default=False)
    
    def to_dict(self):
        return{
            'id': self.id,
            "content":self.content,
            "at": self.at,
            'accessed':self.accessed
        }


with app.app_context():
    db.create_all()


@app.route('/delete_db')
def del_db():
    with app.app_context():
         db.drop_all()
         return 'Deleted'



@app.route('/delete_db')
def del_db():
    with app.app_context():
         db.drop_all()
         return 'Deleted'


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
            id = gen_receipt(result)    
            return jsonify({'result':result, 'id':id}), resp.status_code

def gen_receipt(data):
    d = data.get("data", {})

    ref = d.get("reference", "N/A")
    ce = d.get("customer", {}).get("email", "N/A")
    amt = d.get("amount", 0) / 100
    crn = d.get("currency", "")
    channel = d.get("channel", "").replace("_", " ")
    auth = d.get("authorization") or {}
    bank = auth.get("bank") or "—"
    mobile = auth.get("mobile_money_number") or "—"
    status = d.get("status", "")
    date_str = d.get("paid_at", "") or d.get("paidAt", "")
    try:
        if date_str:
            utc_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            date_local = utc_time + timedelta(hours=3)
            date = date_local.strftime("%Y-%m-%d %H:%M:%S")
        else:
            date = "N/A"
    except Exception:
        date = date_str or "N/A"
    rno = d.get("receipt_number") or generate_random_id(10)

    content = f"""
    <div id="receipt" class="card w-full max-w-lg p-8 rounded-2xl shadow-2xl">
  <div class="flex flex-col items-center mb-6">
    <img src="https://i.ibb.co/KpHnKVW0/LUTAN-LOGO.png" class="h-12 mb-2" alt="Lutan Tech Logo" style="background-color: white;">
    <h2 class="text-2xl font-bold">Payment Receipt</h2>
    <p class="text-gray-400 text-sm">Transaction Reference: <span class="accent">{ref}</span></p>
  </div>

  <div class="border-t border-gray-700 my-4"></div>

  <div class="space-y-3 text-xs">
    <div class="flex justify-between"><span>Customer Email:</span><span style="font-size: xx-small !important;">{ce}</span></div>
    <div class="flex justify-between"><span>Amount:</span><span class="font-semibold">{amt:.2f}</span></div>
    <div class="flex justify-between"><span>Currency:</span><span>{crn}</span></div>
    <div class="flex justify-between"><span>Payment Channel:</span><span>{channel}</span></div>
    <div class="flex justify-between"><span>Bank / Method:</span><span>{bank}</span></div>
    <div class="flex justify-between"><span>Mobile Number:</span><span>{mobile}</span></div>
    <div class="flex justify-between"><span>Status:</span><span style="text-transform:uppercase;">{status}</span></div>
    <div class="flex justify-between"><span>Date Paid:</span><span>{date}</span></div>
    <div class="flex justify-between"><span>Receipt No:</span><span>{rno}</span></div>
  </div>

  <div class="border-t border-gray-700 my-6"></div>

  <p class="text-center text-gray-400 text-sm">
    Thank you for trusting <span class="accent font-medium">Lutan Tech</span> <br>
    This serves as your official payment confirmation.
  </p>

  <div class="text-center mt-6">
    <button id="print"  onclick="window.print()" 
            class="bg-emerald-500 no-print hover:bg-emerald-600 text-white px-6 py-2 rounded-lg text-sm font-semibold transition">
      Print / Download Receipt
    </button>
  </div>
</div>
    """

    try:
        new_r = Receipt(id=generate_random_id(10), content=content)
        db.session.add(new_r)
        db.session.commit()
        return new_r.id
    except Exception as e:
        return f'Database error: {str(e)}'
            id = gen_receipt(result)    
            return jsonify({'result':result, 'id':id}), resp.status_code

def gen_receipt(data):
    d = data.get("data", {})

    ref = d.get("reference", "N/A")
    ce = d.get("customer", {}).get("email", "N/A")
    amt = d.get("amount", 0) / 100
    crn = d.get("currency", "")
    channel = d.get("channel", "").replace("_", " ")
    auth = d.get("authorization") or {}
    bank = auth.get("bank") or "—"
    mobile = auth.get("mobile_money_number") or "—"
    status = d.get("status", "")
    date_str = d.get("paid_at", "") or d.get("paidAt", "")
    try:
        if date_str:
            utc_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            date_local = utc_time + timedelta(hours=3)
            date = date_local.strftime("%Y-%m-%d %H:%M:%S")
        else:
            date = "N/A"
    except Exception:
        date = date_str or "N/A"
    rno = d.get("receipt_number") or generate_random_id(10)

    content = f"""
    <div id="receipt" class="card w-full max-w-lg p-8 rounded-2xl shadow-2xl">
  <div class="flex flex-col items-center mb-6">
    <img src="https://i.ibb.co/KpHnKVW0/LUTAN-LOGO.png" class="h-12 mb-2" alt="Lutan Tech Logo" style="background-color: white;">
    <h2 class="text-2xl font-bold">Payment Receipt</h2>
    <p class="text-gray-400 text-sm">Transaction Reference: <span class="accent">{ref}</span></p>
  </div>

  <div class="border-t border-gray-700 my-4"></div>

  <div class="space-y-3 text-xs">
    <div class="flex justify-between"><span>Customer Email:</span><span style="font-size: xx-small !important;">{ce}</span></div>
    <div class="flex justify-between"><span>Amount:</span><span class="font-semibold">{amt:.2f}</span></div>
    <div class="flex justify-between"><span>Currency:</span><span>{crn}</span></div>
    <div class="flex justify-between"><span>Payment Channel:</span><span>{channel}</span></div>
    <div class="flex justify-between"><span>Bank / Method:</span><span>{bank}</span></div>
    <div class="flex justify-between"><span>Mobile Number:</span><span>{mobile}</span></div>
    <div class="flex justify-between"><span>Status:</span><span style="text-transform:uppercase;">{status}</span></div>
    <div class="flex justify-between"><span>Date Paid:</span><span>{date}</span></div>
    <div class="flex justify-between"><span>Receipt No:</span><span>{rno}</span></div>
  </div>

  <div class="border-t border-gray-700 my-6"></div>

  <p class="text-center text-gray-400 text-sm">
    Thank you for trusting <span class="accent font-medium">Lutan Tech</span> <br>
    This serves as your official payment confirmation.
  </p>

  <div class="text-center mt-6">
    <button id="print"  onclick="window.print()" 
            class="bg-emerald-500 no-print hover:bg-emerald-600 text-white px-6 py-2 rounded-lg text-sm font-semibold transition">
      Print / Download Receipt
    </button>
  </div>
</div>
    """

    try:
        new_r = Receipt(id=generate_random_id(10), content=content)
        db.session.add(new_r)
        db.session.commit()
        return new_r.id
    except Exception as e:
        return f'Database error: {str(e)}'

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
    
    
@app.route('/receipt/<string:id>')
def get_receipt(id):
    if id:
        receipt = Receipt.query.filter_by(id=id).first()
        if receipt:
            return jsonify({'receipt':receipt.to_dict()}), 200
        return jsonify({'error':'Failed to get receipt. \n  Please contact <a href="/support"> support </a> and provide the email message send to you'}), 404
    return jsonify({'error':'Missing data in request. Please use the link send to your email '}), 400
    
    
@app.route('/receipt/<string:id>')
def get_receipt(id):
    if id:
        receipt = Receipt.query.filter_by(id=id).first()
        if receipt:
            return jsonify({'receipt':receipt.to_dict()}), 200
        return jsonify({'error':'Failed to get receipt. \n  Please contact <a href="/support"> support </a> and provide the email message send to you'}), 404
    return jsonify({'error':'Missing data in request. Please use the link send to your email '}), 400

# run
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
