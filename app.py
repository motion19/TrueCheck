from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import sqlite3
import uuid
import qrcode
import io
import base64
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

DATABASE = "truecheck.db"
NETWORK_URL = "https://truecheck-nmr0.onrender.com"


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def setup_database():
    connection = get_db()
    
    connection.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    
    connection.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            batch TEXT,
            barcode TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            event TEXT NOT NULL,
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    columns = [
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(products)"
        ).fetchall()
    ]

    if "barcode" not in columns:
        connection.execute(
            "ALTER TABLE products ADD COLUMN barcode TEXT"
        )

    connection.commit()
    connection.close()


def add_event(product_id, event, actor):
    connection = get_db()

    connection.execute(
        """
        INSERT INTO events
        (product_id, event, actor, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            product_id,
            event,
            actor,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    connection.commit()
    connection.close()

@app.route("/business/register", methods=["GET", "POST"])
def business_register():
    if request.method == "POST":
        business_name = request.form.get(
            "business_name", ""
        ).strip()

        email = request.form.get(
            "email", ""
        ).strip().lower()

        password = request.form.get("password", "")

        role = request.form.get("role", "").strip()

        allowed_roles = [
            "Manufacturer",
            "Wholesaler",
            "Retailer"
        ]

        if not business_name or not email or not password:
            flash("Please complete all required fields.")
            return redirect(url_for("business_register"))

        if role not in allowed_roles:
            flash("Please select a valid business role.")
            return redirect(url_for("business_register"))

        if len(password) < 8:
            flash("Your password must be at least 8 characters.")
            return redirect(url_for("business_register"))

        connection = get_db()

        try:
            connection.execute(
                """
                INSERT INTO businesses
                (business_name, email, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    business_name,
                    email,
                    generate_password_hash(password),
                    role,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
            )

            connection.commit()

        except sqlite3.IntegrityError:
            connection.close()
            flash("This email is already registered.")
            return redirect(url_for("business_register"))

        connection.close()

        flash("Business account created. Please log in.")
        return redirect(url_for("business_login"))

    return render_template("business_register.html")


@app.route("/business/login", methods=["GET", "POST"])
def business_login():
    if request.method == "POST":
        email = request.form.get(
            "email", ""
        ).strip().lower()

        password = request.form.get("password", "")

        connection = get_db()

        business = connection.execute(
            "SELECT * FROM businesses WHERE email = ?",
            (email,)
        ).fetchone()

        connection.close()

        if business and check_password_hash(
            business["password_hash"], password
        ):
            session.clear()
            session["business_id"] = business["id"]
            session["business_name"] = business["business_name"]
            session["role"] = business["role"]

            return redirect(url_for("home"))

        flash("Invalid email or password.")
        return redirect(url_for("business_login"))

    return render_template("business_login.html")


@app.route("/business/logout")
def business_logout():
    session.clear()
    flash("You have logged out.")
    return redirect(url_for("business_login"))

@app.route("/")
def home():
    connection = get_db()

    products = connection.execute(
        "SELECT * FROM products ORDER BY created_at DESC"
    ).fetchall()

    connection.close()

    return render_template(
        "index.html",
        products=products
    )


@app.route("/register", methods=["POST"])
def register_product():
    product_id = (
        "TC-NG-"
        + datetime.now().strftime("%Y%m%d")
        + "-"
        + uuid.uuid4().hex[:8].upper()
    )

    name = request.form.get("name", "").strip()
    batch = request.form.get("batch", "").strip()
    barcode = request.form.get("barcode", "").strip()

    if not name:
        return "Product name is required.", 400

    connection = get_db()

    connection.execute(
        """
        INSERT INTO products
        (id, name, batch, barcode, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            name,
            batch,
            barcode,
            "With manufacturer",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    connection.commit()
    connection.close()

    add_event(
        product_id,
        "Product registered",
        "Manufacturer"
    )

    return redirect(
        url_for("show_qr", product_id=product_id)
    )


@app.route("/qr/<product_id>")
def show_qr(product_id):
    connection = get_db()

    product = connection.execute(
        "SELECT * FROM products WHERE id = ?",
        (product_id,)
    ).fetchone()

    connection.close()

    if product is None:
        return "Product not found.", 404

    verification_url = (
        NETWORK_URL
        + "/verify/"
        + product_id
    )

    qr_image = qrcode.make(verification_url)

    buffer = io.BytesIO()
    qr_image.save(buffer, format="PNG")

    qr_base64 = base64.b64encode(
        buffer.getvalue()
    ).decode("utf-8")

    return render_template(
        "qr.html",
        product=product,
        qr=qr_base64,
        verification_url=verification_url
    )


@app.route("/verify/<product_id>")
def verify_product(product_id):
    connection = get_db()

    product = connection.execute(
        "SELECT * FROM products WHERE id = ?",
        (product_id,)
    ).fetchone()

    events = connection.execute(
        """
        SELECT *
        FROM events
        WHERE product_id = ?
        ORDER BY id
        """,
        (product_id,)
    ).fetchall()

    connection.close()

    if product is None:
        return "Product not found.", 404

    return render_template(
        "verify.html",
        product=product,
        events=events
    )


@app.route("/transfer/<product_id>", methods=["POST"])
def transfer_product(product_id):
    actor = request.form.get(
        "actor",
        "Authorized user"
    ).strip()

    destination = request.form.get(
        "destination",
        ""
    ).strip()

    if not destination:
        return "Destination is required.", 400

    connection = get_db()

    product = connection.execute(
        "SELECT * FROM products WHERE id = ?",
        (product_id,)
    ).fetchone()

    if product is None:
        connection.close()
        return "Product not found.", 404

    new_status = "With " + destination

    connection.execute(
        """
        UPDATE products
        SET status = ?
        WHERE id = ?
        """,
        (new_status, product_id)
    )

    connection.commit()
    connection.close()

    add_event(
        product_id,
        "Transferred to " + destination,
        actor
    )

    return redirect(
        url_for("verify_product", product_id=product_id)
    )


@app.route("/scanner")
def scanner():
    return render_template("scanner.html")


@app.route("/barcode")
def check_barcode():
    barcode = request.args.get("code", "").strip()

    if not barcode:
        return "No barcode was provided.", 400

    connection = get_db()

    product = connection.execute(
        "SELECT id FROM products WHERE barcode = ?",
        (barcode,)
    ).fetchone()

    connection.close()

    if product:
        return redirect(
            url_for(
                "verify_product",
                product_id=product["id"]
            )
        )

    return (
        "This barcode is not registered in TrueCheck. "
        "Authenticity cannot be confirmed."
    )


setup_database()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
