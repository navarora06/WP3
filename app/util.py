from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
import os
from contextlib import contextmanager
from werkzeug.security import generate_password_hash, check_password_hash
from app import extensions
import re


@contextmanager
def db_session():
    db = extensions.SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def hash_password(pw: str) -> str:
    return generate_password_hash(pw)

def verify_password(pw: str, pw_hash: str) -> bool:
    return check_password_hash(pw_hash, pw)

def ensure_dirs(storage_root: str):
    os.makedirs(os.path.join(storage_root, "uploads", "audio"), exist_ok=True)
    os.makedirs(os.path.join(storage_root, "uploads", "docs"), exist_ok=True)
    os.makedirs(os.path.join(storage_root, "reports"), exist_ok=True)


def text_to_pdf_bytes(title: str, text: str) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # Title
    c.setFont("Helvetica-Bold", 14)
    c.drawString(2 * cm, height - 2 * cm, title)

    # Body (simple line wrap)
    c.setFont("Helvetica", 10)
    y = height - 3 * cm
    max_width = width - 4 * cm

    for paragraph in (text or "").split("\n"):
        line = ""
        for word in paragraph.split(" "):
            test = (line + " " + word).strip()
            if c.stringWidth(test, "Helvetica", 10) <= max_width:
                line = test
            else:
                c.drawString(2 * cm, y, line)
                y -= 12
                line = word
                if y < 2 * cm:
                    c.showPage()
                    c.setFont("Helvetica", 10)
                    y = height - 2 * cm
        c.drawString(2 * cm, y, line)
        y -= 12
        if y < 2 * cm:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = height - 2 * cm

    c.save()
    return buf.getvalue()

def safe_filename(name: str) -> str:
    name = name.strip().replace(" ", "_")
    return re.sub(r"[^A-Za-z0-9_\-\.]", "", name)
