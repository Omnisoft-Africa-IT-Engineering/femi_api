"""
apps/femi_account/services.py
Génération des échéances fiscales annuelles pour une entreprise, selon
le calendrier OTR (Office Togolais des Recettes).

⚠️ Les dates ci-dessous sont des valeurs par défaut à valider avec un
comptable ou le calendrier officiel OTR — je ne suis pas en mesure de
garantir qu'elles correspondent exactement à la réglementation en
vigueur. Adapte-les si besoin avant mise en production.

⚠️ RÈGLES DE FILTRAGE PAR TYPE — également à valider :
Au Togo, ce sont en principe le RÉGIME FISCAL (réel/simplifié/
synthétique, généralement basé sur le chiffre d'affaires) qui
détermine les obligations de TVA/TPU, pas la forme juridique en tant
que telle. Une SARL peut être en régime simplifié ; une entreprise
individuelle peut être en régime réel si elle dépasse un seuil de CA.
En l'absence de données fiables sur le régime réel de chaque
entreprise, la règle ci-dessous est volontairement PRUDENTE : elle ne
retire la TVA/TPU QUE pour type_entreprise == 'INDIVIDUEL', et
uniquement si regime_fiscal ne dit pas explicitement le contraire.
Toute entreprise dont le régime fiscal réel diffère de cette
supposition verra des échéances manquantes ou en trop tant que ce
champ n'est pas fiabilisé — à corriger avec un comptable dès que
possible.
"""

from datetime import date

from io import BytesIO
from decimal import Decimal
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm

from django.utils import timezone

from .models import EcheanceFiscale

# Valeurs de regime_fiscal considérées comme "régime réel" (donc TVA/TPU
# dues même pour une entreprise individuelle). Confirmé par requête en
# base (python manage.py shell) : les valeurs réellement utilisées
# aujourd'hui sont None et 'Réel simplifié' — cette dernière EST un
# régime réel (juste avec une déclaration allégée vs "réel normal"),
# donc elle est incluse ici. Si d'autres libellés apparaissent plus
# tard (ex: "Réel normal", "Synthétique"), ajuste cette liste en
# conséquence.
_REGIMES_REELS = {"REEL", "REGIME_REEL", "RÉEL", "RÉEL SIMPLIFIÉ", "REEL SIMPLIFIE", "RÉEL NORMAL", "REEL NORMAL"}


def _est_regime_simplifie(entreprise) -> bool:
    """
    True si l'entreprise doit être exemptée de TVA mensuelle / acomptes
    TPU trimestriels (présomption de régime simplifié/synthétique).
    """
    type_entreprise = (getattr(entreprise, "type_entreprise", "") or "").upper()
    regime_fiscal = (getattr(entreprise, "regime_fiscal", "") or "").upper()

    if regime_fiscal in _REGIMES_REELS:
        # Le régime fiscal déclaré prime sur la forme juridique s'il est
        # explicitement connu et indique un régime réel.
        return False

    return type_entreprise == "INDIVIDUEL"


def _date_debut_suivi(entreprise) -> date:
    """
    Date à partir de laquelle on suit les échéances de cette entreprise :
    son jour de création. Sans ça, une entreprise inscrite en cours
    d'année recevrait toutes les échéances déjà passées depuis janvier,
    aussitôt affichées "en retard" alors qu'elle n'existait pas encore.

    On se base sur la date de création (et non sur la date du jour) pour
    que la génération reste idempotente : la relancer plus tard ne change
    pas le résultat.
    """
    cree_le = getattr(entreprise, "created_at", None)
    if cree_le is None:
        return date.today()
    if timezone.is_aware(cree_le):
        cree_le = timezone.localtime(cree_le)
    return cree_le.date()


def generer_echeances_otr(entreprise, annee: int):
    """
    Crée les échéances fiscales de l'année donnée pour cette entreprise,
    si elles n'existent pas déjà (idempotent : ne duplique pas si la
    tâche est relancée plusieurs fois).

    Les échéances antérieures à la création de l'entreprise ne sont pas
    générées (voir _date_debut_suivi).
    """
    echeances_a_creer = []
    simplifie = _est_regime_simplifie(entreprise)

    # --- TVA : mensuelle, déclarée le 15 du mois suivant ---
    # Non générée en régime simplifié présumé (voir _est_regime_simplifie).
    if not simplifie:
        for mois in range(1, 13):
            mois_declaration = mois + 1 if mois < 12 else 1
            annee_declaration = annee if mois < 12 else annee + 1
            echeances_a_creer.append({
                "type_echeance": "TVA",
                "libelle": f"Déclaration TVA — {mois:02d}/{annee}",
                "date_echeance": date(annee_declaration, mois_declaration, 15),
            })

        # --- TPU / Patente : acomptes trimestriels ---
        # Non générés en régime simplifié présumé, idem TVA.
        trimestres = [
            (3, date(annee, 4, 10)),
            (6, date(annee, 7, 10)),
            (9, date(annee, 10, 10)),
            (12, date(annee + 1, 1, 10)),
        ]
        for num_trimestre, echeance_date in trimestres:
            echeances_a_creer.append({
                "type_echeance": "TPU_ACOMPTE",
                "libelle": f"Acompte TPU/Patente — T{trimestres.index((num_trimestre, echeance_date)) + 1} {annee}",
                "date_echeance": echeance_date,
            })

    # --- Liasse fiscale annuelle (SYSCOHADA) ---
    # Générée pour tout le monde : même en régime simplifié, une
    # déclaration annuelle reste généralement due.
    echeances_a_creer.append({
        "type_echeance": "LIASSE_ANNUELLE",
        "libelle": f"Liasse fiscale SYSCOHADA — exercice {annee}",
        "date_echeance": date(annee + 1, 4, 30),
    })

    # --- Déclaration annuelle des salaires ---
    # Générée pour tout le monde également (uniquement pertinente si
    # l'entreprise a des salariés, mais on n'a pas encore cette donnée
    # pour filtrer plus finement — à ajouter si un champ "a_des_salaries"
    # ou équivalent devient disponible sur Entreprise).
    echeances_a_creer.append({
        "type_echeance": "DAS",
        "libelle": f"Déclaration annuelle des salaires — exercice {annee}",
        "date_echeance": date(annee + 1, 1, 31),
    })

    # --- On ne suit que les échéances postérieures à l'arrivée de l'entreprise ---
    debut_suivi = _date_debut_suivi(entreprise)
    echeances_a_creer = [
        e for e in echeances_a_creer if e["date_echeance"] >= debut_suivi
    ]

    for donnee in echeances_a_creer:
        EcheanceFiscale.objects.get_or_create(
            entreprise=entreprise,
            type_echeance=donnee["type_echeance"],
            date_echeance=donnee["date_echeance"],
            defaults={"libelle": donnee["libelle"]},
        )




class PDFQuoteService:
    """
    Service de génération de devis au format PDF pour Femi (Conforme SYSCOHADA / PME).
    """

    @staticmethod
    def generate_quote_pdf(devis_instance) -> BytesIO:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm
        )
        
        elements = []
        styles = getSampleStyleSheet()
        
        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Heading1'],
            fontSize=22,
            textColor=colors.HexColor('#1E3A8A'),
            spaceAfter=6
        )
        
        normal_style = styles['Normal']
        bold_style = ParagraphStyle(
            'BoldStyle',
            parent=normal_style,
            fontName='Helvetica-Bold'
        )

        company_name = getattr(devis_instance.entreprise, 'name', 'Femi Enterprise')
        company_info = getattr(devis_instance.entreprise, 'address', 'Lomé, Togo')
        
        header_data = [
            [
                Paragraph(f"<b>{company_name}</b><br/>{company_info}", normal_style),
                Paragraph(f"<b>DEVIS N° :</b> {devis_instance.reference}<br/>"
                          f"<b>Date :</b> {devis_instance.created_at.strftime('%d/%m/%Y')}<br/>"
                          f"<b>Statut :</b> {devis_instance.get_status_display()}", normal_style)
            ]
        ]
        header_table = Table(header_data, colWidths=[9 * cm, 8 * cm])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 1 * cm))

        client_name = getattr(devis_instance.client, 'name', 'Client Comptant')
        client_address = getattr(devis_instance.client, 'address', '')
        
        client_data = [
            [Paragraph("<b>Facturer à :</b>", bold_style)],
            [Paragraph(f"<b>{client_name}</b><br/>{client_address}", normal_style)]
        ]
        client_table = Table(client_data, colWidths=[17 * cm])
        client_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F3F4F6')),
            ('PADDING', (0,0), (-1,-1), 10),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        elements.append(client_table)
        elements.append(Spacer(1, 1 * cm))

        table_data = [
            [
                Paragraph("<b>Description</b>", bold_style),
                Paragraph("<b>Qté</b>", bold_style),
                Paragraph("<b>P.U. (FCFA)</b>", bold_style),
                Paragraph("<b>Total HT (FCFA)</b>", bold_style)
            ]
        ]
        
        lines = devis_instance.lignes.all() if hasattr(devis_instance, 'lignes') else []
        for line in lines:
            table_data.append([
                Paragraph(line.description, normal_style),
                str(line.quantity),
                f"{line.unit_price:,.2f}",
                f"{line.total_ht:,.2f}"
            ])
            
        item_table = Table(table_data, colWidths=[8 * cm, 2 * cm, 3.5 * cm, 3.5 * cm])
        item_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#E5E7EB')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#111827')),
            ('ALIGN', (1,0), (-1,-1), 'CENTER'),
            ('ALIGN', (3,0), (-1,-1), 'RIGHT'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D1D5DB')),
        ]))
        elements.append(item_table)
        elements.append(Spacer(1, 0.5 * cm))

        subtotal_ht = getattr(devis_instance, 'total_ht', 0)
        tax_amount = subtotal_ht * Decimal('0.18')
        total_ttc = subtotal_ht + tax_amount

        totals_data = [
            ["Total HT :", f"{subtotal_ht:,.2f} FCFA"],
            ["TVA (18%) :", f"{tax_amount:,.2f} FCFA"],
            ["Total TTC :", f"{total_ttc:,.2f} FCFA"]
        ]
        
        totals_table = Table(totals_data, colWidths=[13 * cm, 4 * cm])
        totals_table.setStyle(TableStyle([
            ('ALIGN', (0,0), (0,-1), 'RIGHT'),
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        elements.append(totals_table)
        elements.append(Spacer(1, 2 * cm))

        footer_data = [
            [
                Paragraph("<b>Cachet et Signature :</b>", normal_style),
                Paragraph("<b>Bon pour accord (Client) :</b>", normal_style)
            ]
        ]
        footer_table = Table(footer_data, colWidths=[8.5 * cm, 8.5 * cm])
        footer_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 40),
        ]))
        elements.append(footer_table)

        doc.build(elements)
        buffer.seek(0)
        return buffer