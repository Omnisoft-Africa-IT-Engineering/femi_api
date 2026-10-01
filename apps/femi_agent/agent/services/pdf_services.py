import io
from django.template.loader import render_to_string
from xhtml2pdf import pisa

def render_devis_pdf(devis):
    """
    Génère un fichier PDF binaire à partir du template HTML du Devis.
    """
    pme = devis.profile          # Récupère l'entreprise émettrice
    lignes = devis.lignes.all()  # Récupère toutes les lignes d'articles

    context = {
        'devis': devis,
        'pme': pme,
        'lignes': lignes,
    }

    html_string = render_to_string('devis/devis_template.html', context)
    result = io.BytesIO()

    pdf = pisa.pisaDocument(io.BytesIO(html_string.encode("UTF-8")), result)

    if not pdf.err:
        return result.getvalue()
    
    return None