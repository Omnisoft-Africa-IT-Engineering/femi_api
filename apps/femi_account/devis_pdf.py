"""Génération du PDF d'un devis (ReportLab).

Les montants sont ceux enregistrés en base (lignes et total calculés par le
backend). Aucune TVA n'est ajoutée ici : le devis affiche le total des lignes
tel que saisi.
"""

import logging
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

import requests
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from django.core.exceptions import ObjectDoesNotExist
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logger = logging.getLogger(__name__)

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


def _profil_facturation(entreprise):
    """Profil d'en-tête / pied de page de l'entreprise, ou None s'il n'existe pas."""
    try:
        return entreprise.profil_facturation
    except ObjectDoesNotExist:
        return None


def _logo_flowable(profil):
    """Image du logo (téléchargée depuis son URL), ou None si absente / injoignable."""
    url = (profil.logo_url or "").strip() if profil else ""
    if not url:
        return None
    try:
        reponse = requests.get(url, timeout=5)
        reponse.raise_for_status()
        octets = reponse.content
        largeur, hauteur = ImageReader(BytesIO(octets)).getSize()
        ratio = min(5 * cm / largeur, 2.2 * cm / hauteur)
        return RLImage(BytesIO(octets), width=largeur * ratio, height=hauteur * ratio, hAlign="LEFT")
    except Exception:
        # Un logo injoignable ne doit jamais empêcher de générer le devis.
        logger.warning("[DevisPDF] Logo inutilisable : %s", url, exc_info=True)
        return None


def _texte_pied(profil) -> str:
    """Texte du pied de page (moyens de paiement, conditions), vide si rien."""
    if profil is None:
        return ""
    morceaux = []
    if (profil.moyens_paiement or "").strip():
        morceaux.append(
            "<b>Moyens de paiement :</b> "
            + escape(profil.moyens_paiement.strip()).replace("\n", "<br/>")
        )
    if (profil.conditions_defaut or "").strip():
        morceaux.append(
            "<b>Conditions :</b> " + escape(profil.conditions_defaut.strip())
        )
    return "<br/>".join(morceaux)


def generer_pdf_devis(devis) -> BytesIO:
    """Retourne le PDF du devis dans un BytesIO positionné au début."""
    entreprise = devis.entreprise
    profil = _profil_facturation(entreprise)
    texte_pied = _texte_pied(profil)
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
        bottomMargin=(3.6 if texte_pied else 2) * cm,
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
    nom_affiche = (profil.nom_commercial or "").strip() if profil else ""
    lignes_entreprise = [f"<b>{escape(nom_affiche or entreprise.nom)}</b>"]
    adresse = ((profil.adresse or "").strip() if profil else "") or getattr(
        entreprise, "adresse", None
    )
    if adresse:
        lignes_entreprise.append(escape(adresse))
    if profil and (profil.telephone_pro or "").strip():
        lignes_entreprise.append(f"Tél : {escape(profil.telephone_pro.strip())}")
    nif_rccm = (profil.nif_rccm or "").strip() if profil else ""
    if nif_rccm:
        lignes_entreprise.append(f"NIF / RCCM : {escape(nif_rccm)}")
    else:
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

    cellule_entreprise = [Paragraph("<br/>".join(lignes_entreprise), normal)]
    logo = _logo_flowable(profil)
    if logo is not None:
        cellule_entreprise = [logo, Spacer(1, 0.2 * cm)] + cellule_entreprise

    entete = Table(
        [[cellule_entreprise,
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

    petit = ParagraphStyle("PiedPage", parent=normal, fontSize=8, leading=10)

    def pied_de_page(canvas, _doc):
        """Dessine le pied de page (moyens de paiement, conditions) sur chaque page."""
        if not texte_pied:
            return
        paragraphe = Paragraph(texte_pied, petit)
        _, hauteur = paragraphe.wrap(A4[0] - 4 * cm, 3 * cm)
        canvas.saveState()
        canvas.setStrokeColor(_GRIS_BORDURE)
        canvas.line(2 * cm, 1.2 * cm + hauteur + 0.2 * cm, A4[0] - 2 * cm, 1.2 * cm + hauteur + 0.2 * cm)
        paragraphe.drawOn(canvas, 2 * cm, 1.2 * cm)
        canvas.restoreState()

    doc.build(elements, onFirstPage=pied_de_page, onLaterPages=pied_de_page)
    buffer.seek(0)
    return buffer