import sys
import os
import io
import tempfile
import base64
import pandas as pd
import fitz  # PyMuPDF
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLineEdit, QSplitter,
                             QTableWidget, QTableWidgetItem, QFileDialog, QFormLayout,
                             QMessageBox, QHeaderView, QTextEdit, QLabel, QScrollArea)
from PyQt6.QtGui import QImage, QPixmap, QKeySequence
from PyQt6.QtCore import Qt

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors

# Import the embedded logo string
try:
    from logo_base64 import LOGO_B64
except ImportError:
    LOGO_B64 = None


class CustomTableWidget(QTableWidget):
    """A custom table widget that supports Excel-style Copy & Paste."""

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
        elif event.matches(QKeySequence.StandardKey.Paste):
            self.paste_selection()
        else:
            super().keyPressEvent(event)

    def copy_selection(self):
        selection = self.selectedIndexes()
        if not selection:
            return

        rows = sorted(set(index.row() for index in selection))
        cols = sorted(set(index.column() for index in selection))

        copy_text = ""
        for r in rows:
            row_data = []
            for c in cols:
                item = self.item(r, c)
                row_data.append(item.text() if item else "")
            copy_text += "\t".join(row_data) + "\n"

        QApplication.clipboard().setText(copy_text.strip("\n"))

    def paste_selection(self):
        selection = self.selectedIndexes()
        if not selection:
            return

        start_row = selection[0].row()
        start_col = selection[0].column()
        paste_text = QApplication.clipboard().text()

        if not paste_text:
            return

        lines = paste_text.split("\n")

        self.blockSignals(True)

        for r, line in enumerate(lines):
            curr_row = start_row + r
            if curr_row >= self.rowCount():
                self.insertRow(self.rowCount())

            cols = line.split("\t")
            for c, text in enumerate(cols):
                curr_col = start_col + c
                if curr_col < self.columnCount():
                    self.setItem(curr_row, curr_col, QTableWidgetItem(text))

            try:
                qty_item = self.item(curr_row, 2)
                price_item = self.item(curr_row, 3)
                qty = float(qty_item.text()) if qty_item and qty_item.text() else 0.0
                price = float(price_item.text()) if price_item and price_item.text() else 0.0
                amount = qty * price
                self.setItem(curr_row, 4, QTableWidgetItem(f"{amount:.2f}"))
            except ValueError:
                pass

        self.blockSignals(False)

        main_win = self.window()
        if hasattr(main_win, 'update_preview'):
            main_win.update_preview()


class InvoiceEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Red Summit - 出單易")
        self.resize(1400, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # --- LEFT PANEL ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        self.btn_load = QPushButton("📄 Load Customer CSV")
        self.btn_load.clicked.connect(self.load_csv)
        self.btn_load.setMinimumHeight(45)
        self.btn_load.setStyleSheet("font-weight: bold; font-size: 13px;")
        left_layout.addWidget(self.btn_load)

        form_layout = QFormLayout()
        self.fields = {}
        form_data = [
            ("Invoice Number:", "invoice_num"),
            ("Date:", "date"),
            ("Customer Name:", "customer_name"),
            ("Attn:", "attn"),
            ("Phone:", "phone"),
            ("Loading Port:", "loading_port"),
            ("Discharge Port:", "discharge_port"),
            ("ETD:", "etd"),
            ("Incoterm:", "incoterm"),
            ("Payment Term:", "payment_term"),
            ("Currency:", "currency"),
            ("Your Reference (PO):", "po_number")
        ]

        for label, key in form_data:
            self.fields[key] = QLineEdit()
            self.fields[key].textChanged.connect(self.update_preview)
            form_layout.addRow(label, self.fields[key])

        self.fields['address'] = QTextEdit()
        self.fields['address'].setMaximumHeight(60)
        self.fields['address'].textChanged.connect(self.update_preview)
        form_layout.insertRow(3, "Address:", self.fields['address'])

        left_layout.addLayout(form_layout)

        table_controls = QHBoxLayout()
        self.btn_add_row = QPushButton("+ Add Row")
        self.btn_add_row.clicked.connect(self.add_row)
        self.btn_delete_row = QPushButton("- Delete Row")
        self.btn_delete_row.clicked.connect(self.delete_row)
        table_controls.addWidget(self.btn_add_row)
        table_controls.addWidget(self.btn_delete_row)
        left_layout.addLayout(table_controls)

        # Table expanded to 5 columns. Description (index 1) is set to stretch.
        self.table = CustomTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ITEM UPC", "DESCRIPTION", "QUANTITY", "UNIT PRICE", "AMOUNT"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.calculate_amount)
        left_layout.addWidget(self.table)

        # --- Editable Bank Information Field ---
        bank_label = QLabel("Bank in information:")
        bank_label.setStyleSheet("font-weight: bold; margin-top: 5px;")
        left_layout.addWidget(bank_label)

        self.fields['bank_info'] = QTextEdit()
        self.fields['bank_info'].setMaximumHeight(90)
        self.fields['bank_info'].setPlainText(
            "Beneficiary Banker: HSBC HONG KONG\n"
            "Beneficiary Banker Address: No.1 Queen's Road Central, Central, Hong Kong\n"
            "Beneficiary Account Name: Red Summit Limited\n"
            "Beneficiary Account Number: 747008514838\n"
            "SWIFT CODE: HSBCHKHHHKH"
        )
        self.fields['bank_info'].textChanged.connect(self.update_preview)
        left_layout.addWidget(self.fields['bank_info'])

        bottom_buttons = QHBoxLayout()
        self.btn_preview = QPushButton("Refresh Preview")
        self.btn_preview.clicked.connect(self.update_preview)
        self.btn_preview.setMinimumHeight(50)

        self.btn_save = QPushButton("Save As & Generate PDF")
        self.btn_save.clicked.connect(self.save_pdf)
        self.btn_save.setMinimumHeight(50)

        bottom_buttons.addWidget(self.btn_preview)
        bottom_buttons.addWidget(self.btn_save)
        left_layout.addLayout(bottom_buttons)

        # --- RIGHT PANEL ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        preview_label = QLabel("PDF Preview")
        preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(preview_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.preview_layout = QVBoxLayout(self.scroll_content)
        self.preview_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.scroll_area.setWidget(self.scroll_content)

        right_layout.addWidget(self.scroll_area)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([600, 800])

    def calculate_amount(self, item):
        col = item.column()
        # QTY is now col 2, PRICE is col 3
        if col in [2, 3]:
            row = item.row()

            self.table.blockSignals(True)
            try:
                qty_item = self.table.item(row, 2)
                price_item = self.table.item(row, 3)

                qty = float(qty_item.text()) if qty_item and qty_item.text() else 0.0
                price = float(price_item.text()) if price_item and price_item.text() else 0.0
                amount = qty * price

                # AMOUNT is now col 4
                if not self.table.item(row, 4):
                    self.table.setItem(row, 4, QTableWidgetItem(f"{amount:.2f}"))
                else:
                    self.table.item(row, 4).setText(f"{amount:.2f}")
            except ValueError:
                pass

            self.table.blockSignals(False)
            self.update_preview()

    def add_row(self):
        row_count = self.table.rowCount()
        self.table.insertRow(row_count)

    def delete_row(self):
        current_row = self.table.currentRow()
        if current_row >= 0:
            self.table.removeRow(current_row)
            self.update_preview()

    def load_csv(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Purchase Order CSV", "", "CSV Files (*.csv)")
        if not file_path:
            return

        try:
            df = pd.read_csv(file_path)
            po_num = str(df['Document Number'].iloc[0])
            raw_date = str(df['Date'].iloc[0])

            try:
                parsed_date = datetime.strptime(raw_date, "%m/%d/%Y")
                formatted_date = parsed_date.strftime("%d/%b/%Y")
                inv_suffix = parsed_date.strftime("%Y%m%d")
            except ValueError:
                formatted_date = raw_date
                inv_suffix = "UNKNOWN"

            for key in self.fields:
                self.fields[key].blockSignals(True)
            self.table.blockSignals(True)

            self.fields['invoice_num'].setText(f"RSL{inv_suffix}PI")
            self.fields['date'].setText(formatted_date)
            self.fields['po_number'].setText(f"PO {po_num}")
            self.fields['customer_name'].setText("Cesium Telecom")
            self.fields['address'].setPlainText("5798 Ferrier\nMont-Royal\nQC H4P 1M7\nCanada")
            self.fields['attn'].setText("Camilo Florez")
            self.fields['phone'].setText("+1 877-798-8686")
            self.fields['loading_port'].setText("Hong Kong, Hong Kong")
            self.fields['discharge_port'].setText("Canada, Canada")
            self.fields['etd'].setText("14/Sep/2026")
            self.fields['incoterm'].setText("[EXW] EX WORKS")
            self.fields['payment_term'].setText("100% T/T Before Shipment")
            self.fields['currency'].setText("USD")

            self.table.setRowCount(0)
            for index, row in df.iterrows():
                self.table.insertRow(index)

                # Safely get 'Item UPC', defaults to empty string if missing in CSV
                upc_value = str(row.get('Item UPC', ''))

                self.table.setItem(index, 0, QTableWidgetItem(upc_value))
                self.table.setItem(index, 1, QTableWidgetItem(str(row['Description'])))
                self.table.setItem(index, 2, QTableWidgetItem(str(row['Ordered'])))
                self.table.setItem(index, 3, QTableWidgetItem(f"{float(row['Unit Price']):.2f}"))
                self.table.setItem(index, 4, QTableWidgetItem(f"{float(row['Amount']):.2f}"))

            for key in self.fields:
                self.fields[key].blockSignals(False)
            self.table.blockSignals(False)

            self.update_preview()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not read CSV: {str(e)}")

    def build_pdf_document(self, output_path):
        doc = SimpleDocTemplate(
            output_path,
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
                img_buffer = io.BytesIO(img_data)
                img = Image(img_buffer, width=240, height=150, kind='proportional')
                img.hAlign = 'LEFT'

                logo_cell = [Spacer(1, -50), img]
                header_data.append([logo_cell, address_paragraph])

            except Exception as e:
                err_text = Paragraph(f"<b>(Error rendering embedded logo)</b>", normal_style)
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
        elements.append(Spacer(1, 15))

        # --- PARAGRAPH 1 (Title) ---
        elements.append(Paragraph(f"<b>Pro-Forma Invoice # {self.fields['invoice_num'].text()}</b>", title_style))

        # --- TABLE 1 (Customer Info) ---
        addr = self.fields['address'].toPlainText().replace('\n', '<br/>')

        left_col_data = [
            [Paragraph("Customer Name:", normal_style), Paragraph(self.fields['customer_name'].text(), normal_style)],
            [Paragraph("Address:", normal_style), Paragraph(addr, normal_style)],
            [Paragraph("Attn:", normal_style), Paragraph(self.fields['attn'].text(), normal_style)],
            [Paragraph("Phone:", normal_style), Paragraph(self.fields['phone'].text(), normal_style)]
        ]
        left_table = Table(left_col_data, colWidths=[100, 200])
        left_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('PADDING', (0, 0), (-1, -1), 2)]))

        right_col_data = [
            [Paragraph("Loading Port:", normal_style), Paragraph(self.fields['loading_port'].text(), normal_style)],
            [Paragraph("Discharge Port:", normal_style), Paragraph(self.fields['discharge_port'].text(), normal_style)],
            [Paragraph("ETD:", normal_style), Paragraph(self.fields['etd'].text(), normal_style)],
            [Paragraph("Incoterm:", normal_style), Paragraph(self.fields['incoterm'].text(), normal_style)],
            [Paragraph("Payment Term:", normal_style), Paragraph(self.fields['payment_term'].text(), normal_style)],
            [Paragraph("Currency:", normal_style), Paragraph(self.fields['currency'].text(), normal_style)]
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

        # --- PARAGRAPH 2 (Reference & Date - Tight Stacked Layout) ---
        ref_date_data = [
            [Paragraph("<b>Your Reference:</b>", normal_style), Paragraph("<b>Date:</b>", normal_style)],
            [Paragraph(self.fields['po_number'].text(), normal_style),
             Paragraph(self.fields['date'].text(), normal_style)]
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

        # --- TABLE 2 (Items) ---
        table_data = [[
            Paragraph("ITEM UPC", bold_style),
            Paragraph("DESCRIPTION", bold_style),
            Paragraph("QUANTITY", bold_style),
            Paragraph("UNIT<br/>PRICE", bold_style),
            Paragraph("AMOUNT", bold_style)
        ]]

        total_amt = 0.0
        row_count = self.table.rowCount()

        for row in range(row_count):
            upc = self.table.item(row, 0).text() if self.table.item(row, 0) else ""
            desc = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
            qty = self.table.item(row, 2).text() if self.table.item(row, 2) else ""
            price = self.table.item(row, 3).text() if self.table.item(row, 3) else ""
            amt_str = self.table.item(row, 4).text() if self.table.item(row, 4) else "0.00"

            try:
                clean_amt = amt_str.replace("$", "").replace(",", "")
                total_amt += float(clean_amt)
                formatted_amt = f"${float(clean_amt):.2f}"
            except ValueError:
                formatted_amt = "$0.00"

            table_data.append([
                Paragraph(upc, normal_style),
                Paragraph(desc, normal_style),
                Paragraph(qty, normal_style),
                Paragraph(price, normal_style),
                Paragraph(formatted_amt, normal_style)
            ])

        table_data.append(
            ["", "", "", Paragraph("Subtotal", normal_style), Paragraph(f"${total_amt:,.2f}", normal_style)])
        table_data.append(["", "", "", Paragraph("Total", bold_style), Paragraph(f"${total_amt:,.2f}", bold_style)])

        # Split the previous 315 point description width to accommodate UPC (95 points) and Description (220 points)
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

        # --- KeepTogether Dynamic Bank Information ---
        bank_block = []
        bank_block.append(Spacer(1, 42))
        bank_block.append(Paragraph("Bank in information:", bold_style))

        # Pull text from the editable box and format newlines for ReportLab
        dynamic_bank_text = self.fields['bank_info'].toPlainText().replace('\n', '<br/>')
        bank_block.append(Paragraph(dynamic_bank_text, normal_style))

        elements.append(KeepTogether(bank_block))

        def draw_footer(canvas, doc):
            canvas.saveState()
            canvas.setFont('Helvetica', 10)
            canvas.drawString(35, 20, f"Page: {doc.page}")
            canvas.restoreState()

        doc.build(elements, onFirstPage=draw_footer, onLaterPages=draw_footer)

    def update_preview(self):
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)

            self.build_pdf_document(temp_path)

            for i in reversed(range(self.preview_layout.count())):
                widget = self.preview_layout.itemAt(i).widget()
                if widget:
                    widget.setParent(None)

            pdf_doc = fitz.open(temp_path)
            for page in pdf_doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                fmt = QImage.Format.Format_RGB888 if pix.alpha == 0 else QImage.Format.Format_RGBA8888
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)

                lbl = QLabel()
                lbl.setPixmap(QPixmap.fromImage(img))
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet("border: 1px solid #ccc; margin: 10px;")
                self.preview_layout.addWidget(lbl)

            pdf_doc.close()
            os.remove(temp_path)

        except Exception as e:
            pass

    def save_pdf(self):
        default_filename = f"PRO-FORMA - {self.fields['invoice_num'].text()}.pdf"
        save_path, _ = QFileDialog.getSaveFileName(self, "Save Invoice PDF", default_filename, "PDF Files (*.pdf)")

        if not save_path:
            return

        try:
            self.build_pdf_document(save_path)
            QMessageBox.information(self, "Success", f"Invoice successfully saved at:\n{save_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to generate PDF: {str(e)}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = InvoiceEditor()
    window.show()
    sys.exit(app.exec())
