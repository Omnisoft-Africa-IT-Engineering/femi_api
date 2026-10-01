import os
import datetime
from decimal import Decimal
from django.conf import settings
from django.template.loader import render_to_string
from .models import Devis, LigneDevis


def generer_echeances_otr(pme_profile):
    """
    Génère ou met à jour les échéances fiscales OTR pour le profil PME.
    Requis par les signaux de l'application femi_account (signals.py).
    """
    pass


def render_devis_pdf(devis_id):
    """
    Génère et retourne le chemin d'un fichier PDF pour un devis existant.
    """
    from xhtml2pdf import pisa

    try:
        devis = Devis.objects.get(id=devis_id)
    except Devis.DoesNotExist:
        return None

    context = {
        'devis': devis,
        'pme': devis.profile,
        'lignes': devis.lignes.all(),
    }

    html_string = render_to_string('devis/template_pdf.html', context)
    pdf_dir = os.path.join(settings.MEDIA_ROOT, 'devis_pdf')
    os.makedirs(pdf_dir, exist_ok=True)

    pdf_path = os.path.join(pdf_dir, f"devis_{devis.id}.pdf")

    with open(pdf_path, 'wb') as pdf_file:
        pisa_status = pisa.CreatePDF(html_string, dest=pdf_file)

    if pisa_status.err:
        raise Exception("Erreur lors de la génération du PDF avec xhtml2pdf")

    # Mise à jour du champ s'il existe dans le modèle Devis
    if hasattr(devis, 'fichier_pdf'):
        devis.fichier_pdf = f"devis_pdf/devis_{devis.id}.pdf"
        devis.save(update_fields=['fichier_pdf'])

    return pdf_path


def creer_devis_et_pdf(pme_profile, donnees_gemini):
    """
    Crée une instance de Devis en BDD à partir des données extraites par l'agent Gemini,
    puis génère le PDF associé.
    """
    annee = datetime.date.today().year
    count = Devis.objects.filter(profile=pme_profile, created_at__year=annee).count() + 1
    numero_devis = f"DEV-{annee}-{count:03d}"

    devis = Devis.objects.create(
        profile=pme_profile,
        numero_devis=numero_devis,
        client_nom=donnees_gemini.get('client_nom') or 'Client Passager',
        client_telephone=donnees_gemini.get('client_telephone') or '',
        statut=Devis.StatutDevis.BROUILLON
    )

    montant_total = Decimal('0.00')
    for art in donnees_gemini.get('articles', []):
        ligne = LigneDevis.objects.create(
            devis=devis,
            designation=art.get('designation', ''),
            quantite=art.get('quantite', 1.00),
            prix_unitaire=art.get('prix_unitaire', 0.00)
        )
        montant_total += (ligne.total_ligne or Decimal('0.00'))

    devis.montant_total = montant_total
    devis.save(update_fields=['montant_total'])

    render_devis_pdf(devis.id)
    return devis