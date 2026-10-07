import streamlit as st
import pandas as pd
import base64
import io
import json
import re
from datetime import datetime

import pdfplumber

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors

# Import the embedded logo string
try:
    from logo_base64 import LOGO_B64
except ImportError:
    LOGO_B64 = None

# --- Page Configuration ---
st.set_page_config(page_title="Red Summit - 出單易", layout="wide")
st.title("Red Summit - 出單易 (Web Version)")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("Red Summit - 出單易")
    with st.form("login_form"):
        password = st.text_input("Password", type="password", max_chars=6)
        submitted = st.form_submit_button("Log in")

    if submitted:
        if password == "291213":
            st.session_state.authenticated = True
        else:
            st.error("Incorrect password.")

    if not st.session_state.authenticated:
        st.stop()
        
# --- Session State Initialization ---
if 'invoice_data' not in st.session_state:
    st.session_state.invoice_data = {
        'invoice_num': '', 'date': '', 'customer_name': '',
        'address': '', 'attn': '', 'phone': '',
        'loading_port': '', 'discharge_port': '', 'etd': '',
        'incoterm': '', 'payment_term': '', 'currency': '', 'po_number': '',
        'bank_info': "Beneficiary Banker: HSBC HONG KONG\nBeneficiary Banker Address: No.1 Queen's Road Central, Central, Hong Kong\nBeneficiary Account Name: Red Summit Limited\nBeneficiary Account Number: 747008514838\nSWIFT CODE: HSBCHKHHHKH"
    }

EDIT_COLUMNS = ["L", "ITEM UPC", "DESCRIPTION", "QUANTITY", "UNIT PRICE"]
ALL_COLUMNS = EDIT_COLUMNS + ["AMOUNT"]

if 'items_df' not in st.session_state:
    st.session_state.items_df = pd.DataFrame(columns=ALL_COLUMNS)


def _find_pdf_value(text, labels):
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})\s*[:#-]?\s*(.+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_po_number(text):
    match = re.search(r"\bPO\s*[-:#]?\s*(\d+)\b", text, re.IGNORECASE)
    return match.group(1) if match else ""


def _parse_pdf_purchase_order(uploaded_file):
    with pdfplumber.open(io.BytesIO(uploaded_file.getvalue())) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if not full_text.strip():
            raise ValueError("The PDF does not contain extractable text.")
            
        po_num = _extract_po_number(full_text)
        raw_date = _find_pdf_value(full_text, ["Date", "Order Date"])

        rows = []
        l_counter = 1
        current_item = None

        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
                
            # 1. Block Slicing: Chop off everything above the table headers
            header_match = re.search(r"(Item\s*UPC|UPC\b)", text, re.IGNORECASE)
            if header_match:
                text = text[header_match.start():]
                
            # 2. Block Slicing: Chop off everything below the table (Page Footers, Totals, Addresses, PO Notes)
            footer_match = re.search(r"\n\s*(Page\s+\d+|Total\b|Subtotal\b|Currency\b|Purchase Order\b|Please note\b|Cesium\b)", text, re.IGNORECASE)
            if footer_match:
                text = text[:footer_match.start()]
                
            # 3. Parse the cleanly sliced table block
            for line in text.splitlines():
                clean_line = re.sub(r'\s*\|\s*', '  ', line).strip()
                if not clean_line:
                    continue
                    
                # Ignore leftover header rows
                if re.search(r"^(?:L\b|Item\b|UPC\b|Description\b|Ordered\b|Received\b|Amount\b|Qty on\b|Unit Pri)", clean_line, re.IGNORECASE):
                    continue
                    
                # Find a new item via a 13-digit UPC
                match = re.search(r"^(?:(\d+)\s+)?(\d{13})\s+(.+)$", clean_line)
                if match:
                    if current_item:
                        rows.append(current_item)
                    
                    line_no = match.group(1)
                    upc = match.group(2)
                    rest = match.group(3)
                    
                    # Extract the numbers at the end of the line
                    num_match = re.search(r"(.*?)\s+(\d+)\s+(\d+)\s*(.*?)\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})$", rest)
                    if num_match:
                        current_item = {
                            "L": line_no if line_no else str(l_counter),
                            "Item UPC": upc,
                            "Description": num_match.group(1).strip(),
                            "Ordered": num_match.group(2),
                            "Unit Price": num_match.group(5),
                            "Amount": num_match.group(6)
                        }
                    else:
                        current_item = {
                            "L": line_no if line_no else str(l_counter),
                            "Item UPC": upc,
                            "Description": rest.strip(),
                            "Ordered": "", "Unit Price": "", "Amount": ""
                        }
                    l_counter += 1
                elif current_item:
                    # If numbers fell onto a separate line, catch them
                    if not current_item["Ordered"]:
                        num_match = re.search(r"^(?:.*?)\s*(\d+)\s+(\d+)\s*(.*?)\s+(\d+\.\d{2})\s+([\d,]+\.\d{2})$", clean_line)
                        if num_match:
                            current_item["Ordered"] = num_match.group(1)
                            current_item["Unit Price"] = num_match.group(4)
                            current_item["Amount"] = num_match.group(5)
                            continue
                    
                    # Inline failsafe: Actively chop off any footer text that got mashed into the description line
                    footer_inline_match = re.search(r"(Purchase Order\b|Please note\b|Total\b|Page \d+|Cesium|Vendor Bill To)", clean_line, re.IGNORECASE)
                    if footer_inline_match:
                        clean_line = clean_line[:footer_inline_match.start()].strip()
                    
                    # Any remaining text safely belongs to the description (footers are already sliced away)
                    if clean_line:
                        current_item["Description"] += " " + clean_line

        # Commit the very last item found in the document
        if current_item:
            rows.append(current_item)

        final_rows = []
        for r in rows:
            if r["Ordered"]:
                # Generically remove internal 3-4 digit SKUs (e.g., 118-2869)
                clean_desc = re.sub(r'\b\d{3}-\d{4}\b', '', r["Description"])
                r["Description"] = re.sub(r'\s+', ' ', clean_desc).strip()
                final_rows.append(r)

        if not final_rows:
            raise ValueError("No items could be extracted from this PDF. Check if the format has significantly changed.")

        return pd.DataFrame(final_rows), po_num, raw_date


def load_purchase_order(uploaded_file):
    if uploaded_file.name.lower().endswith(".pdf"):
        return _parse_pdf_purchase_order(uploaded_file)
    df = pd.read_csv(uploaded_file)
    po_num = str(df['Document Number'].iloc[0]) if 'Document Number' in df.columns else ""
    raw_date = str(df['Date'].iloc[0]) if 'Date' in df.columns else ""
    return df, po_num, raw_date


def _split_cell_lines(value):
    return [part.strip() for part in str(value or "").splitlines() if part.strip()]


def _split_description_items(value, row_count):
    description = re.sub(r"\s+", " ", str(value or "")).strip()
    item_markers = list(re.finditer(r"(?=\b\d{2}[A-Za-z][A-Za-z0-9]+\b)", description))
    if len(item_markers) == row_count:
        starts = [marker.start() for marker in item_markers]
        return [description[start:end].strip() for start, end in zip(starts, starts[1:] + [len(description)])]

    description_lines = _split_cell_lines(value)
    return description_lines if len(description_lines) == row_count else [description]


def _expand_uploaded_rows(df):
    numeric_columns = ["L", "Item UPC", "Ordered", "Unit Price", "Amount"]
    for _, row in df.iterrows():
        split_values = {column: _split_cell_lines(row.get(column, "")) for column in numeric_columns}
        line_numbers = split_values["L"]
        row_count = len(line_numbers) if len(line_numbers) > 1 else max((len(values) for values in split_values.values()), default=1)
        if row_count <= 1:
            yield row
            continue

        description_parts = _split_description_items(row.get("Description", ""), row_count)
        for index in range(row_count):
            expanded_row = row.copy()
            for column, values in split_values.items():
                expanded_row[column] = values[index] if index < len(values) else ""
            if len(description_parts) == row_count:
                expanded_row["Description"] = description_parts[index]
            yield expanded_row


def _format_number(value, default="0.00"):
    try:
        return f"{float(str(value).replace('$', '').replace(',', '').strip()):.2f}"
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    """Tolerant numeric parse: handles $ , blanks and None without raising."""
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _file_signature(uploaded_file):
    """Cheap identity for an uploaded file: its name and byte length.

    st.file_uploader returns the same object on every rerun, so a content
    signature is what tells a genuinely new upload apart from a re-render of
    the previous one. Reading the bytes is cheap next to re-parsing the PDF.
    """
    try:
        return (uploaded_file.name, len(uploaded_file.getvalue()))
    except Exception:
        return None


def _sync_amounts():
    """Fold pending cell edits into the frame and recompute AMOUNT.

    Runs as the data_editor's on_change callback, which Streamlit dispatches
    *before* the script body (session_state.on_script_will_rerun runs ahead of
    ctx.on_script_start()). st.data_editor paints from whatever frame it is
    handed, so a value computed in the body can only appear on the *next* run.
    Doing the work here means the editor is handed an already-corrected frame
    and the new AMOUNT renders immediately, with no st.rerun() in between -- a
    rerun is what previously discarded in-flight edits.

    st.session_state["items_editor"] holds the raw DataEditorState
    ({edited_rows, added_rows, deleted_rows}), not a DataFrame, so the pending
    cell edits are applied here explicitly. Rows are added/removed with the
    buttons below rather than inline, so added/deleted rows stay empty.
    """
    state = st.session_state.get("items_editor") or {}
    edited_rows = state.get("edited_rows") or {}
    if not edited_rows:
        return

    base = st.session_state.get("items_df")
    if base is None or base.empty:
        return

    updated = base.copy()
    for row_index, cell_edits in edited_rows.items():
        if not isinstance(cell_edits, dict):
            continue
        # Guard against a stale row index from a frame that has since shrunk.
        if not isinstance(row_index, int) or row_index >= len(updated.index):
            continue
        for column, value in cell_edits.items():
            if column in updated.columns:
                updated.iat[row_index, updated.columns.get_loc(column)] = value

    updated["AMOUNT"] = _with_amounts(updated[EDIT_COLUMNS])["AMOUNT"]
    st.session_state.items_df = updated


def _with_amounts(edit_df):
    """Derive a display frame with a computed AMOUNT column.

    The returned frame is a *new* object; edit_df is never mutated. This keeps
    the frame handed to st.data_editor byte-stable across reruns, which is what
    allows the editor to keep its cell edits alive.
    """
    display_df = edit_df.copy()
    if display_df.empty:
        display_df["AMOUNT"] = []
        return display_df
    display_df["AMOUNT"] = [
        f"{_to_float(q) * _to_float(p):.2f}"
        for q, p in zip(display_df.get("QUANTITY", ""), display_df.get("UNIT PRICE", ""))
    ]
    return display_df


def _cached_pdf(fields, items_df):
    """Render the PDF only when the underlying data actually changed.

    Re-rendering an identical PDF would make the embedded viewer reload and lose
    its scroll position on every unrelated interaction.
    """
    signature = (
        json.dumps(fields, sort_keys=True, default=str),
        items_df.to_json(orient="records"),
    )
    if st.session_state.get("_pdf_sig") == signature:
        return st.session_state["_pdf_bytes"]

    pdf_bytes = build_pdf_document(fields, items_df).getvalue()
    st.session_state["_pdf_sig"] = signature
    st.session_state["_pdf_bytes"] = pdf_bytes
    return pdf_bytes


# --- PDF Generation Function ---
def build_pdf_document(fields, items_df):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=35, leftMargin=35, topMargin=35, bottomMargin=40)
    elements = []

    normal_style = ParagraphStyle('NormalStyle', fontName='Helvetica', fontSize=10, leading=14)
    bold_style = ParagraphStyle('BoldStyle', fontName='Helvetica-Bold', fontSize=10, leading=14)
    title_style = ParagraphStyle('TitleStyle', fontName='Helvetica-Bold', fontSize=18, spaceAfter=20)
    right_align_style = ParagraphStyle('RightAlign', parent=normal_style, alignment=2)

    # Header with Embedded Logo
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
            img_buffer = io.BytesIO(img_data)
            img = Image(img_buffer, width=240, height=150, kind='proportional')
            img.hAlign = 'LEFT'
            logo_cell = [Spacer(1, -50), img]
            header_data.append([logo_cell, address_paragraph])
        except Exception as e:
            err_text = Paragraph("<b>(Error rendering embedded logo)</b>", normal_style)
            header_data.append([err_text, address_paragraph])
    else:
        err_text = Paragraph("<b>(Embedded Logo Not Found)</b><br/>Please generate logo_base64.py", normal_style)
        header_data.append([err_text, address_paragraph])

    header_table = Table(header_data, colWidths=[200, 325])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (0, 0), -25)
    ]))

    elements.append(header_table)
    elements.append(Spacer(1, 4))

    # Title
    title_table = Table([[
        Paragraph(f"<b>Pro-Forma Invoice # {fields['invoice_num']}</b>", title_style)
    ]], colWidths=[525])
    title_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(title_table)
    elements.append(Spacer(1, 18))

    # Customer Info Tables
    addr = fields['address'].replace('\n', '<br/>')
    left_col_data = [
        [Paragraph("Customer Name:", normal_style), Paragraph(fields['customer_name'], normal_style)],
        [Paragraph("Address:", normal_style), Paragraph(addr, normal_style)],
        [Paragraph("Attn:", normal_style), Paragraph(fields['attn'], normal_style)],
        [Paragraph("Phone:", normal_style), Paragraph(fields['phone'], normal_style)]
    ]
    left_table = Table(left_col_data, colWidths=[100, 200])
    left_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('PADDING', (0, 0), (-1, -1), 2)]))

    right_col_data = [
        [Paragraph("Loading Port:", normal_style), Paragraph(fields['loading_port'], normal_style)],
        [Paragraph("Discharge Port:", normal_style), Paragraph(fields['discharge_port'], normal_style)],
        [Paragraph("ETD:", normal_style), Paragraph(fields['etd'], normal_style)],
        [Paragraph("Incoterm:", normal_style), Paragraph(fields['incoterm'], normal_style)],
        [Paragraph("Payment Term:", normal_style), Paragraph(fields['payment_term'], normal_style)],
        [Paragraph("Currency:", normal_style), Paragraph(fields['currency'], normal_style)]
    ]
    right_table = Table(right_col_data, colWidths=[100, 125])
    right_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('PADDING', (0, 0), (-1, -1), 2)]))

    master_info_table = Table([[left_table, right_table]], colWidths=[300, 225])
    master_info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0)
    ]))
    elements.append(master_info_table)
    elements.append(Spacer(1, 18))

    # Your Reference / Date (below customer info, ref left, date right)
    po_value = re.sub(r"^\s*PO\s*", "", str(fields['po_number']), flags=re.IGNORECASE).strip()
    ref_date_data = [
        [Paragraph("<b>Your Reference:</b>", normal_style), Paragraph("<b>Date:</b>", normal_style)],
        [Paragraph(f"PO {po_value}", normal_style), Paragraph(str(fields['date']), normal_style)]
    ]
    ref_date_table = Table(ref_date_data, colWidths=[300, 225])
    ref_date_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (1, 0), 0),
        ('BOTTOMPADDING', (0, 0), (1, 0), 0),
        ('TOPPADDING', (0, 1), (1, 1), 0)
    ]))
    elements.append(ref_date_table)
    elements.append(Spacer(1, 15))

    # Line Items
    table_data = [[
        Paragraph("L", bold_style),
        Paragraph("ITEM UPC", bold_style),
        Paragraph("DESCRIPTION", bold_style),
        Paragraph("QUANTITY", bold_style),
        Paragraph("UNIT<br/>PRICE", bold_style),
        Paragraph("AMOUNT", bold_style)
    ]]

    total_amt = 0.0
    for index, (_, row) in enumerate(items_df.iterrows(), start=1):
        line_number = str(row.get("L", "")) or str(index)
        upc = str(row.get("ITEM UPC", ""))
        desc = str(row.get("DESCRIPTION", ""))
        qty = str(row.get("QUANTITY", "0"))
        price = str(row.get("UNIT PRICE", "0.00"))

        line_amt = _to_float(qty) * _to_float(price)
        total_amt += line_amt
        formatted_amt = f"${line_amt:,.2f}"

        table_data.append([
            Paragraph(line_number, normal_style),
            Paragraph(upc, normal_style),
            Paragraph(desc, normal_style),
            Paragraph(qty, normal_style),
            Paragraph(price, normal_style),
            Paragraph(formatted_amt, normal_style)
        ])

    table_data.append(["", "", "", "", Paragraph("Subtotal", normal_style), Paragraph(f"${total_amt:,.2f}", normal_style)])
    table_data.append(["", "", "", "", Paragraph("Total", bold_style), Paragraph(f"${total_amt:,.2f}", bold_style)])

    item_table = Table(table_data, colWidths=[30, 105, 165, 65, 75, 85])
    item_styles = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -3), 1, colors.black),
        ('PADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (3, 0), (5, -1), 'CENTER'),
    ]
    if len(items_df) > 0:
        item_styles.append(('BACKGROUND', (5, 1), (5, len(items_df)), colors.HexColor("#E0E0E0")))

    item_table.setStyle(TableStyle(item_styles))
    elements.append(item_table)

    # Dynamic Bank Info
    bank_block = []
    bank_block.append(Spacer(1, 42))
    bank_block.append(Paragraph("Bank in information:", bold_style))
    dynamic_bank_text = fields['bank_info'].replace('\n', '<br/>')
    bank_block.append(Paragraph(dynamic_bank_text, normal_style))
    elements.append(KeepTogether(bank_block))

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 10)
        canvas.drawString(35, 20, f"Page: {doc.page}")
        canvas.restoreState()

    doc.build(elements, onFirstPage=draw_footer, onLaterPages=draw_footer)
    buffer.seek(0)
    return buffer


# --- UI Layout ---
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. Load Customer Purchase Order")
    uploaded_file = st.file_uploader("Upload Purchase Order", type=['csv', 'pdf'])

    # Only parse when a *new* file arrives. The uploader keeps returning the
    # same file object on every rerun, so re-parsing here would reset items_df
    # and wipe out live cell edits (and the AMOUNT the on_change callback just
    # computed) the moment the user touched a cell.
    if (uploaded_file is not None
            and st.session_state.get("loaded_po_signature") != _file_signature(uploaded_file)):
        st.session_state["loaded_po_signature"] = _file_signature(uploaded_file)
        try:
            df, po_num, raw_date = load_purchase_order(uploaded_file)
        except (ValueError, ImportError) as error:
            st.error(f"Could not read purchase order: {error}")
            st.stop()

        try:
            parsed_date = datetime.strptime(raw_date, "%m/%d/%Y")
            formatted_date = parsed_date.strftime("%d/%b/%Y")
            inv_suffix = parsed_date.strftime("%Y%m%d")
        except ValueError:
            formatted_date = raw_date
            inv_suffix = "UNKNOWN"

        st.session_state.invoice_data.update({
            'invoice_num': f"RSL{inv_suffix}PI",
            'date': formatted_date,
            'customer_name': "Cesium Telecom",
            'address': "5798 Ferrier\nMont-Royal\nQC H4P 1M7\nCanada",
            'attn': "Camilo Florez",
            'phone': "+1 877-798-8686",
            'loading_port': "Hong Kong, Hong Kong",
            'discharge_port': "Canada, Canada",
            'etd': "14/Sep/2026",
            'incoterm': "[EXW] EX WORKS",
            'payment_term': "100% T/T Before Shipment",
            'currency': "USD",
            'po_number': po_num
        })

        new_items = []
        for index, row in enumerate(_expand_uploaded_rows(df), start=1):
            new_items.append({
                "L": str(row.get('L', '')) or str(index),
                "ITEM UPC": re.sub(r"\s+", "", str(row.get('Item UPC', ''))),
                "DESCRIPTION": str(row.get('Description', '')),
                "QUANTITY": str(row.get('Ordered', '0')),
                "UNIT PRICE": _format_number(row.get('Unit Price', 0)) if 'Unit Price' in df.columns else "0.00",
            })
        loaded = pd.DataFrame(new_items, columns=EDIT_COLUMNS)
        # Seed AMOUNT up front so the first render already shows correct totals.
        loaded["AMOUNT"] = _with_amounts(loaded)["AMOUNT"]
        st.session_state.items_df = loaded

    st.subheader("2. Invoice Details")
    d = st.session_state.invoice_data
    d['invoice_num'] = st.text_input("Invoice Number:", d['invoice_num'])
    d['date'] = st.text_input("Date:", d['date'])
    d['customer_name'] = st.text_input("Customer Name:", d['customer_name'])
    d['address'] = st.text_area("Address:", d['address'])
    d['attn'] = st.text_input("Attn:", d['attn'])
    d['phone'] = st.text_input("Phone:", d['phone'])

    with st.expander("Shipping & Payment Terms"):
        d['loading_port'] = st.text_input("Loading Port:", d['loading_port'])
        d['discharge_port'] = st.text_input("Discharge Port:", d['discharge_port'])
        d['etd'] = st.text_input("ETD:", d['etd'])
        d['incoterm'] = st.text_input("Incoterm:", d['incoterm'])
        d['payment_term'] = st.text_input("Payment Term:", d['payment_term'])
        d['currency'] = st.text_input("Currency:", d['currency'])
        d['po_number'] = st.text_input("Your Reference (PO):", d['po_number'])

    st.subheader("3. Bank Information")
    d['bank_info'] = st.text_area("Edit Bank Details:", d['bank_info'], height=130)


with col2:
    st.subheader("4. Line Items")
    st.write("You can copy and paste directly into this table.")

    # Single editable table. AMOUNT is a read-only column inside the editor, so
    # qty/price stay directly editable and the total updates in the same view.
    #
    # num_rows="fixed" with an explicit key makes Streamlit derive the widget
    # identity from the data SCHEMA (columns, dtypes, row count) instead of the
    # cell values. That is what lets a recalculated AMOUNT be written back into
    # the same frame without resetting the widget and discarding the edit you
    # are typing. Row add/remove is handled by the buttons below, which change
    # the row count and therefore remount cleanly.
    edited_df = st.data_editor(
        st.session_state.items_df,
        key="items_editor",
        num_rows="fixed",
        width="stretch",
        hide_index=True,
        on_change=_sync_amounts,
        column_config={
            "AMOUNT": st.column_config.TextColumn("AMOUNT", disabled=True,
                                                  help="Auto-calculated (Qty x Price)"),
        },
    )

    for column in ALL_COLUMNS:
        if column not in edited_df.columns:
            edited_df[column] = ""

    edited_df = edited_df[ALL_COLUMNS].copy()

    # Recompute AMOUNT from the frame the editor actually returned. This must
    # happen before the session-state write below: writing edited_df back first
    # would clobber the corrected frame that _sync_amounts prepared during the
    # on_change callback, and the AMOUNT column would lag by one edit.
    edited_df["AMOUNT"] = _with_amounts(edited_df[EDIT_COLUMNS])["AMOUNT"]
    st.session_state.items_df = edited_df

    display_df = edited_df

    add_col, del_col = st.columns(2)
    if add_col.button("+ Add Row", width="stretch"):
        blank = {column: "" for column in EDIT_COLUMNS}
        blank["AMOUNT"] = "0.00"
        st.session_state.items_df = pd.concat(
            [st.session_state.items_df, pd.DataFrame([blank], columns=ALL_COLUMNS)],
            ignore_index=True,
        )
        st.rerun()
    if del_col.button("- Delete Last Row", width="stretch", disabled=st.session_state.items_df.empty):
        st.session_state.items_df = st.session_state.items_df.iloc[:-1].reset_index(drop=True)
        st.rerun()

    st.subheader("5. Generate PDF")

    if display_df.empty:
        st.info("Add at least one line item to generate the invoice.")
    else:
        pdf_bytes = _cached_pdf(st.session_state.invoice_data, display_df)

        st.download_button(
            label="⬇️ Download Invoice PDF",
            data=pdf_bytes,
            file_name=f"PRO-FORMA - {st.session_state.invoice_data['invoice_num']}.pdf",
            mime="application/pdf",
        )

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as preview_pdf:
            num_pages = len(preview_pdf.pages)
            with st.container(height=800, border=True):
                for idx, page in enumerate(preview_pdf.pages, start=1):
                    st.image(
                        page.to_image(resolution=150).original,
                        caption=f"Page {idx}" if num_pages > 1 else None,
                        use_container_width=True,
                    )

