"""
Tool de profil contact, pour CUSTOMER_AGENT (customer_prompt.py).

Le prompt marquait get_contact_info(contact) comme "[NON RÉSOLU : le
contenu exact retourné n'est pas encore défini côté backend]". Décisions
prises pour lever ce flou (validées avant écriture, voir conversation) :

    1. L'historique ne couvre QUE les ventes/achats (RECETTE/DEPENSE) liés
       au contact — jamais les PRET_DONNE/PRET_RECU, pour respecter la
       séparation stricte CUSTOMER (dette client) vs ACCOUNTING (prêt)
       imposée par customer_prompt.py section 2.
    2. Historique limité aux 10 dernières opérations (date décroissante),
       + un total_operations pour signaler qu'il y en a potentiellement
       plus — pensé pour une réponse WhatsApp concise, pas un export
       comptable complet.
    3. Le résumé de créance ouverte (total_du, has_open_debt) est inclus
       par convenience, en réutilisant get_contact_open_debts() plutôt que
       de dupliquer la requête — le détail ligne par ligne reste du
       ressort de get_contact_open_debts, appelable séparément.
"""

from apps.femi_account.models import Contact, Operation
from .debts import get_contact_open_debts

HISTORIQUE_LIMITE = 10


def get_contact_info(entreprise, contact):
    """
    Profil d'un contact : identité, résumé de créance ouverte, historique
    récent des ventes/achats (hors prêts).

    Args:
        entreprise: instance Entreprise.
        contact: instance Contact (jamais None ici — la résolution du nom
                 en instance Contact, et la gestion des homonymes via
                 {contacts_correspondants}, est faite en amont par le
                 backend avant d'appeler ce tool).

    Returns:
        {
            "contact": "<nom>",
            "telephone": str | None,
            "type": "CLIENT" | "FOURNISSEUR",
            "creance": {"total_du": float, "has_open_debt": bool},
            "historique": [
                {"operation_id", "transaction_type", "date",
                 "amount_ttc", "description"}, ...
            ],   # 10 dernières, date décroissante ; RECETTE/DEPENSE uniquement
            "total_operations": int,   # nombre total d'opérations (avant limite)
        }
    """
    creance = get_contact_open_debts(entreprise, contact)

    qs = Operation.objects.filter(
        entreprise=entreprise,
        contact=contact,
        transaction_type__in=["RECETTE", "DEPENSE"],
    ).order_by("-transaction_date")

    total_operations = qs.count()
    historique = [
        {
            "operation_id": str(op.id),
            "transaction_type": op.transaction_type,
            "date": op.transaction_date.isoformat(),
            "amount_ttc": float(op.amount_ttc),
            "description": op.description,
        }
        for op in qs[:HISTORIQUE_LIMITE]
    ]

    return {
        "contact": contact.nom,
        "telephone": contact.telephone,
        "type": contact.type,
        "creance": {"total_du": creance["total_du"], "has_open_debt": creance["has_open_debt"]},
        "historique": historique,
        "total_operations": total_operations,
    }
    
    

# tools/contacts.py — nouvelles fonctions

def find_matching_contacts(entreprise, message_text: str) -> list[Contact]:
    """
    Recherche floue littérale : contacts de l'entreprise dont le nom apparaît
    (insensible à la casse) dans message_text. Calculée AVANT le 1er appel LLM
    de CUSTOMER, car le LLM n'a pas encore extrait de nom à ce stade — la
    recherche porte donc sur le texte brut, pas sur un nom déjà identifié.
    Ne gère pas les quasi-homonymes phonétiques (hors scope) — correspondance
    littérale sous-texte uniquement.

    Returns: liste de Contact (vide si aucun match), triée par nom.
    """
    texte = message_text.lower()
    return [c for c in Contact.objects.filter(entreprise=entreprise).order_by("nom") if c.nom.lower() in texte]


def format_contacts_correspondants(contacts: list[Contact]) -> str:
    """Texte à injecter dans {contacts_correspondants} — convention simple,
    même esprit que get_categories_disponibles. "" si aucun contact."""
    return ", ".join(c.nom for c in contacts)


def resolve_contact_for_read(entreprise, nom_contact: str | None) -> Contact | None:
    """
    Résout un nom (déjà extrait par le LLM à l'étape tool_selection) en
    instance Contact, pour les tools READ. JAMAIS d'auto-création (contraire
    à _resolve_contact d'accounting_manager.py, réservé à ACCOUNTING/CREATE).
    N'est appelée qu'après que l'étape validation ait déjà écarté l'ambiguïté
    — retourne None si 0 ou >1 correspondance (filet de sécurité, pas un cas
    normal si validation a fait son travail).
    """
    if not nom_contact:
        return None
    matches = Contact.objects.filter(entreprise=entreprise, nom__iexact=nom_contact)
    return matches.first() if matches.count() == 1 else None