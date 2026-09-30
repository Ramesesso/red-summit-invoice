from fastapi.responses import FileResponse
import io
import base64
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# Import the embedded logo string if available
try:
    from logo_base64 import LOGO_B64
except ImportError:
    LOGO_B64 = None

app = FastAPI(title="Red Summit Invoice API")

# --- ALLOW WEB PAGES TO CONNECT (CORS) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows any web frontend to connect
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- DATA MODELS (What the frontend will send) ---
class InvoiceItem(BaseModel):
    upc: str
    description: str
    quantity: float
    unit_price: float


class InvoiceData(BaseModel):
    invoice_num: str
    date: str
    customer_name: str
    address: str
    attn: str
    phone: str
    loading_port: str
    discharge_port: str
    etd: str
    incoterm: str
    payment_term: str
    currency: str
    po_number: str
    bank_info: str
    items: List[InvoiceItem]


# --- PDF GENERATION ENDPOINT ---
@app.post("/generate_pdf")
def generate_pdf(data: InvoiceData):
    pdf_buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=A4,
        rightMargin=35, leftMargin=35,
        topMargin=35, bottomMargin=40
    )
    elements = []

    normal_style = ParagraphStyle('NormalStyle', fontName='Helvetica', fontSize=10, leading=14)
    bold_style = ParagraphStyle('BoldStyle', fontName='Helvetica-Bold', fontSize=10, leading=14)
    title_style = ParagraphStyle('TitleStyle', fontName='Helvetica-Bold', fontSize=18, spaceAfter=20)
    right_align_style = ParagraphStyle('RightAlign', parent=normal_style, alignment=2)

    # --- HEADER (Embedded Logo) ---
    address_paragraph = Paragraph(
        "<b>Red Summit Limited</b><br/>"
        "RM12-13, 29/F ASIA TRADE CENTRE<br/>"
        "79-89 LEI MUK ROAD<br/>"
        "KWAI CHUNG, N.T. Hong Kong",
        right_align_style
    )

    header_data = []

    if LOGO_B64:
        try:
            img_data = base64.b64decode(LOGO_B64)
            img_buffer_io = io.BytesIO(img_data)
            img = Image(img_buffer_io, width=240, height=150, kind='proportional')
            img.hAlign = 'LEFT'
            logo_cell = [Spacer(1, -50), img]
            header_data.append([logo_cell, address_paragraph])
        except Exception:
            err_text = Paragraph("<b>(Error rendering embedded logo)</b>", normal_style)
            header_data.append([err_text, address_paragraph])
    else:
        err_text = Paragraph("<b>(Embedded Logo Not Found)</b>", normal_style)
        header_data.append([err_text, address_paragraph])

    header_table = Table(header_data, colWidths=[200, 325])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (0, 0), -25)
    ]))

    elements.append(header_table)
    elements.append(Spacer(1, 15))

    # --- TITLE ---
    elements.append(Paragraph(f"<b>Pro-Forma Invoice # {data.invoice_num}</b>", title_style))

    # --- CUSTOMER & SHIPPING INFO ---
    addr = data.address.replace('\n', '<br/>')

    left_col_data = [
        [Paragraph("Customer Name:", normal_style), Paragraph(data.customer_name, normal_style)],
        [Paragraph("Address:", normal_style), Paragraph(addr, normal_style)],
        [Paragraph("Attn:", normal_style), Paragraph(data.attn, normal_style)],
        [Paragraph("Phone:", normal_style), Paragraph(data.phone, normal_style)]
    ]
    left_table = Table(left_col_data, colWidths=[100, 200])
    left_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('PADDING', (0, 0), (-1, -1), 2)]))

    right_col_data = [
        [Paragraph("Loading Port:", normal_style), Paragraph(data.loading_port, normal_style)],
        [Paragraph("Discharge Port:", normal_style), Paragraph(data.discharge_port, normal_style)],
        [Paragraph("ETD:", normal_style), Paragraph(data.etd, normal_style)],
        [Paragraph("Incoterm:", normal_style), Paragraph(data.incoterm, normal_style)],
        [Paragraph("Payment Term:", normal_style), Paragraph(data.payment_term, normal_style)],
        [Paragraph("Currency:", normal_style), Paragraph(data.currency, normal_style)]
    ]
    right_table = Table(right_col_data, colWidths=[100, 125])
    right_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('PADDING', (0, 0), (-1, -1), 2)]))

    master_info_table = Table([[left_table, right_table]], colWidths=[300, 225])
    master_info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0)
    ]))
    elements.append(master_info_table)
    elements.append(Spacer(1, 15))

    # --- REF & DATE ---
    ref_date_data = [
        [Paragraph("<b>Your Reference:</b>", normal_style), Paragraph("<b>Date:</b>", normal_style)],
        [Paragraph(data.po_number, normal_style), Paragraph(data.date, normal_style)]
    ]
    ref_table = Table(ref_date_data, colWidths=[262, 263])
    ref_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 0),
        ('TOPPADDING', (0, 1), (-1, 1), 0),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 10),
    ]))
    elements.append(ref_table)

    # --- ITEMS TABLE ---
    table_data = [[
        Paragraph("ITEM UPC", bold_style),
        Paragraph("DESCRIPTION", bold_style),
        Paragraph("QUANTITY", bold_style),
        Paragraph("UNIT<br/>PRICE", bold_style),
        Paragraph("AMOUNT", bold_style)
    ]]

    total_amt = 0.0
    row_count = len(data.items)

    for item in data.items:
        amount = item.quantity * item.unit_price
        total_amt += amount

        table_data.append([
            Paragraph(item.upc, normal_style),
            Paragraph(item.description, normal_style),
            Paragraph(str(item.quantity), normal_style),
            Paragraph(f"{item.unit_price:.2f}", normal_style),
            Paragraph(f"${amount:.2f}", normal_style)
        ])

    table_data.append(["", "", "", Paragraph("Subtotal", normal_style), Paragraph(f"${total_amt:,.2f}", normal_style)])
    table_data.append(["", "", "", Paragraph("Total", bold_style), Paragraph(f"${total_amt:,.2f}", bold_style)])

    item_table = Table(table_data, colWidths=[95, 220, 70, 70, 70])
    item_styles = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -3), 1, colors.black),
        ('PADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (2, 0), (4, -1), 'CENTER'),
    ]

    if row_count > 0:
        item_styles.append(('BACKGROUND', (4, 1), (4, row_count), colors.HexColor("#E0E0E0")))

    item_table.setStyle(TableStyle(item_styles))
    elements.append(item_table)

    # --- BANK INFO ---
    bank_block = [Spacer(1, 42), Paragraph("Bank in information:", bold_style)]
    dynamic_bank_text = data.bank_info.replace('\n', '<br/>')
    bank_block.append(Paragraph(dynamic_bank_text, normal_style))
    elements.append(KeepTogether(bank_block))

    # --- FOOTER ---
    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 10)
        canvas.drawString(35, 20, f"Page: {doc.page}")
        canvas.restoreState()

    doc.build(elements, onFirstPage=draw_footer, onLaterPages=draw_footer)

    pdf_value = pdf_buffer.getvalue()
    pdf_buffer.close()

    return Response(content=pdf_value, media_type="application/pdf")

@app.get("/")
def serve_frontend():
    return FileResponse("index.html")
