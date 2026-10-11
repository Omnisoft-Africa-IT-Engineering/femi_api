"""Génération du PDF d'un devis (ReportLab).

Les montants sont ceux enregistrés en base (lignes et total calculés par le
backend). Aucune TVA n'est ajoutée ici : le devis affiche le total des lignes
tel que saisi.
"""

from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_BLEU = colors.HexColor("#1E3A8A")
_GRIS_CLAIR = colors.HexColor("#F3F4F6")
_GRIS_ENTETE = colors.HexColor("#E5E7EB")
_GRIS_BORDURE = colors.HexColor("#D1D5DB")

_LIBELLES_STATUT = {
    "BROUILLON": "Brouillon",
    "VALIDE": "Validé",
    "FACTURE": "Transformé en facture",
    "ANNULE": "Annulé",
}


def _montant(valeur) -> str:
    """1250000.5 -> '1 250 000,50' (format français)."""
    texte = f"{Decimal(valeur):,.2f}"
    return texte.replace(",", " ").replace(".", ",")


def _nombre(valeur) -> str:
    """Quantité : retire les décimales inutiles (2.00 -> '2')."""
    texte = f"{Decimal(valeur):f}"
    if "." in texte:
        texte = texte.rstrip("0").rstrip(".")
    return texte.replace(".", ",")


def _p(texte, style) -> Paragraph:
    return Paragraph(escape(str(texte)).replace("\n", "<br/>"), style)


def generer_pdf_devis(devis) -> BytesIO:
    """Retourne le PDF du devis dans un BytesIO positionné au début."""
    entreprise = devis.entreprise
    client = devis.client
    devise = getattr(entreprise, "devise", None) or "FCFA"
    devise = {"XOF": "FCFA", "XAF": "FCFA"}.get(devise.upper(), devise)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"Devis {devis.reference}",
    )

    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    gras = ParagraphStyle("Gras", parent=normal, fontName="Helvetica-Bold")
    titre = ParagraphStyle(
        "TitreDevis", parent=styles["Heading1"], fontSize=22, textColor=_BLEU, spaceAfter=6
    )
    droite = ParagraphStyle("Droite", parent=normal, alignment=2)

    elements = [Paragraph("DEVIS", titre)]

    # --- En-tête : entreprise / références du devis -----------------------
    lignes_entreprise = [f"<b>{escape(entreprise.nom)}</b>"]
    if getattr(entreprise, "adresse", None):
        lignes_entreprise.append(escape(entreprise.adresse))
    if getattr(entreprise, "ifu", None):
        lignes_entreprise.append(f"IFU : {escape(entreprise.ifu)}")
    if getattr(entreprise, "rccm", None):
        lignes_entreprise.append(f"RCCM : {escape(entreprise.rccm)}")

    lignes_devis = [
        f"<b>Devis n° :</b> {escape(devis.reference)}",
        f"<b>Date :</b> {devis.date_emission:%d/%m/%Y}",
    ]
    if devis.date_validite:
        lignes_devis.append(f"<b>Valable jusqu'au :</b> {devis.date_validite:%d/%m/%Y}")
    lignes_devis.append(
        f"<b>Statut :</b> {_LIBELLES_STATUT.get(devis.statut, devis.statut)}"
    )

    entete = Table(
        [[Paragraph("<br/>".join(lignes_entreprise), normal),
          Paragraph("<br/>".join(lignes_devis), normal)]],
        colWidths=[9 * cm, 8 * cm],
    )
    entete.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements += [entete, Spacer(1, 0.8 * cm)]

    # --- Client ---------------------------------------------------------
    texte_client = f"<b>{escape(client.nom)}</b>" if client else "<b>Client non précisé</b>"
    if client and getattr(client, "telephone", None):
        texte_client += f"<br/>{escape(client.telephone)}"
    bloc_client = Table(
        [[Paragraph("<b>Destinataire :</b>", gras)], [Paragraph(texte_client, normal)]],
        colWidths=[17 * cm],
    )
    bloc_client.setStyle(
        TableStyle([("BACKGROUND", (0, 0), (-1, -1), _GRIS_CLAIR), ("PADDING", (0, 0), (-1, -1), 10)])
    )
    elements += [bloc_client, Spacer(1, 0.8 * cm)]

    # --- Lignes -----------------------------------------------------------
    donnees = [[
        Paragraph("<b>Description</b>", gras),
        Paragraph("<b>Qté</b>", gras),
        Paragraph(f"<b>P.U. ({escape(devise)})</b>", gras),
        Paragraph(f"<b>Total ({escape(devise)})</b>", gras),
    ]]
    for ligne in devis.lignes.all():
        donnees.append([
            _p(ligne.description, normal),
            _nombre(ligne.quantite),
            _montant(ligne.prix_unitaire),
            _montant(ligne.montant_total),
        ])
    tableau = Table(donnees, colWidths=[7.5 * cm, 2 * cm, 3.75 * cm, 3.75 * cm], repeatRows=1)
    tableau.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _GRIS_ENTETE),
        ("ALIGN", (1, 1), (1, -1), "CENTER"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, _GRIS_BORDURE),
    ]))
    elements += [tableau, Spacer(1, 0.5 * cm)]

    # --- Total ----------------------------------------------------------
    total = Table(
        [[Paragraph("<b>TOTAL</b>", droite), Paragraph(f"<b>{_montant(devis.montant_total)} {escape(devise)}</b>", droite)]],
        colWidths=[13 * cm, 4 * cm],
    )
    total.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1, _BLEU)]))
    elements += [total, Spacer(1, 1.8 * cm)]

    # --- Signatures -------------------------------------------------------
    signatures = Table(
        [[Paragraph("<b>Cachet et signature :</b>", normal),
          Paragraph("<b>Bon pour accord (client) :</b>", normal)]],
        colWidths=[8.5 * cm, 8.5 * cm],
    )
    signatures.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 40)]))
    elements.append(signatures)

    doc.build(elements)
    buffer.seek(0)
    return buffer
