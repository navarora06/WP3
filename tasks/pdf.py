import os
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

def render_gap_report_pdf(output_path: str, title: str, summary: dict, items: list[dict]):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4

    y = height - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, title)
    y -= 25

    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"Summary: {summary}")
    y -= 20

    for idx, it in enumerate(items, start=1):
        text = f"{idx}. [{it['label']}] ({it['confidence']:.2f}) {it['claim']}"
        if y < 80:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 10)
        c.drawString(50, y, text[:120])
        y -= 14

    c.save()
