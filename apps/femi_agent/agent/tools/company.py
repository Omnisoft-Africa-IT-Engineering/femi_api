"""
Tools de lecture de l'agent GENERAL (« conseiller entreprise »).

Destinés à être appelés par GENERAL_PROMPT (prompts/general_prompt.py) via
ToolLoopExecutor. Lecture seule : aucun de ces tools ne crée, ne modifie ni
ne supprime quoi que ce soit.

Noms et paramètres EXACTEMENT ceux annoncés au LLM par general_prompt.py :

    get_company_profile()
    get_top_clients(periode, limite)
    get_top_suppliers(periode, limite)
    get_top_categories(type_operation, periode, limite)
    list_prestations()
    search_operations(mot_cle, contact, transaction_type, periode, limite)

`entreprise` n'est jamais un paramètre choisi par le LLM : il est lié à chaque
tool par functools.partial dans general_executor.py, comme pour
financials.py et customer_executor.py. Chaque requête filtre sur cette
entreprise : aucune fuite possible entre tenants.

Règles de conception :
- Tous les chiffres de CA / dépenses / bénéfice / créances / dettes viennent
  de apps.femi_account.kpi_service (source unique des KPI) : le chat donne
  les mêmes montants que le tableau de bord.
- AUCUN de ces tools ne renvoie la clé `resume_texte` : ToolLoopExecutor
  l'utiliserait telle quelle et court-circuiterait la synthèse du LLM, ce qui
  empêcherait de combiner plusieurs tools dans une même réponse.
- Chaque tool accepte **_ignored : si le LLM ajoute un paramètre inattendu,
  le tool répond quand même au lieu de lever une TypeError.
- Les périodes utilisent le vocabulaire de tools/periodes.py (aujourd'hui,
  hier, cette_semaine, semaine_derniere, ce_mois, mois_dernier, cette_annee,
  annee_derniere, depuis_debut, personnalisee, YYYY). Une période inconnue
  lève ValueError, que ToolLoopExecutor._run_tools transforme en erreur
  générique pour le LLM (jamais de repli silencieux sur « tout l'historique »).
"""

from decimal import Decimal

from django.db.models import Count, Max, Min, Q, Sum

from apps.femi_account.kpi_service import (
    KpiContexte,
    calc_benefice,
    calc_chiffre_affaires,
    calc_creances,
    calc_depenses,
    calc_dettes_fournisseurs,
)
from apps.femi_account.models import Categorie, Contact, Operation, Prestation
from apps.femi_agent.agent.tools.financials import (  # formats partagés avec le chat financier
    _devise_affiche,
    _fmt_montant,
    _label_periode,
)
from apps.femi_agent.agent.tools.periodes import resolve_periode

TOOL_NAMES = (
    "get_company_profile",
    "get_top_clients",
    "get_top_suppliers",
    "get_top_categories",
    "list_prestations",
    "search_operations",
)

PERIODE_PAR_DEFAUT_CLASSEMENT = "depuis_debut"
LIMITE_PAR_DEFAUT = 5
LIMITE_MAX = 15
MOT_CLE_MAX_CHARS = 100
SANS_CATEGORIE = "Sans catégorie"

_TYPES_OPERATION = {"RECETTE", "DEPENSE", "PRET_DONNE", "PRET_RECU"}


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------

def _limite(valeur, defaut=LIMITE_PAR_DEFAUT) -> int:
    """Entier borné entre 1 et LIMITE_MAX (le LLM peut envoyer « 5 » en texte)."""
    try:
        n = int(valeur)
    except (TypeError, ValueError):
        return defaut
    return max(1, min(n, LIMITE_MAX))


def _montant(entreprise, valeur) -> dict:
    """Montant au format {montant, affiche} : le LLM recopie `affiche`."""
    nombre = float(valeur or 0)
    return {
        "montant": round(nombre, 2),
        "affiche": f"{_fmt_montant(nombre)} {_devise_affiche(entreprise)}",
    }


def _texte(valeur, max_chars=None):
    """Chaîne nettoyée, ou None si vide."""
    if valeur is None:
        return None
    texte = str(valeur).strip()
    if not texte:
        return None
    return texte[:max_chars] if max_chars else texte


def _operations_periode(entreprise, periode, date_debut=None, date_fin=None):
    """QuerySet Operation de l'entreprise filtré sur la période (ou tout)."""
    start, end = resolve_periode(periode, date_debut, date_fin)
    qs = Operation.objects.filter(entreprise=entreprise)
    if start is not None:
        qs = qs.filter(transaction_date__gte=start, transaction_date__lte=end)
    return qs


def _somme(qs, champ="amount_ttc") -> Decimal:
    return qs.aggregate(t=Sum(champ))["t"] or Decimal("0")


# --------------------------------------------------------------------------
# Tools exposés au LLM
# --------------------------------------------------------------------------

def get_company_profile(entreprise, **_ignored):
    """
    Fiche de l'entreprise, calculée par le code : identité, volume d'activité,
    chiffres du mois en cours, soldes en cours (créances / dettes).

    Toujours exécuté par general_executor.py, quel que soit le choix du LLM :
    toute réponse de GENERAL s'appuie donc sur cette fiche.
    """
    operations = Operation.objects.filter(entreprise=entreprise)
    activite = operations.aggregate(
        nombre=Count("id"),
        premiere=Min("transaction_date"),
        derniere=Max("transaction_date"),
    )
    contacts = Contact.objects.filter(entreprise=entreprise).aggregate(
        clients=Count("id", filter=Q(type="CLIENT")),
        fournisseurs=Count("id", filter=Q(type="FOURNISSEUR")),
    )
    prestations_actives = Prestation.objects.filter(
        entreprise=entreprise, actif=True
    ).count()
    nombre_categories = Categorie.objects.filter(entreprise=entreprise).count()

    debut, fin = resolve_periode("ce_mois")
    ctx_mois = KpiContexte(entreprise, debut, fin)
    ctx_total = KpiContexte(entreprise, None, None)

    secteur = entreprise.secteur.nom if entreprise.secteur_id else None
    cree_le = entreprise.created_at.date().isoformat() if entreprise.created_at else None

    return {
        "success": True,
        "entreprise": {
            "nom": entreprise.nom,
            "secteur": secteur,
            "type_activite": entreprise.type_activite,
            "type_entreprise": entreprise.type_entreprise,
            "regime_fiscal": entreprise.regime_fiscal,
            "devise": _devise_affiche(entreprise),
            "adresse": entreprise.adresse,
            "rccm": entreprise.rccm,
            "ifu": entreprise.ifu,
            "creee_sur_femi_le": cree_le,
        },
        "activite": {
            "nombre_operations": activite["nombre"] or 0,
            "premiere_operation": (
                activite["premiere"].isoformat() if activite["premiere"] else None
            ),
            "derniere_operation": (
                activite["derniere"].isoformat() if activite["derniere"] else None
            ),
            "clients_enregistres": contacts["clients"] or 0,
            "fournisseurs_enregistres": contacts["fournisseurs"] or 0,
            "prestations_actives": prestations_actives,
            "categories_declarees": nombre_categories,
        },
        "chiffres_ce_mois": {
            "periode": _label_periode("ce_mois"),
            "chiffre_affaires": _montant(entreprise, calc_chiffre_affaires(ctx_mois)),
            "depenses": _montant(entreprise, calc_depenses(ctx_mois)),
            "benefice": _montant(entreprise, calc_benefice(ctx_mois)),
        },
        "soldes_en_cours": {
            "creances_clients": _montant(entreprise, calc_creances(ctx_total)),
            "dettes_fournisseurs": _montant(
                entreprise, calc_dettes_fournisseurs(ctx_total)
            ),
        },
    }


def _classement_contacts(
    entreprise, type_operation, periode, limite, date_debut, date_fin
):
    """Classement des contacts par total, pour un type d'opération donné."""
    periode = periode or PERIODE_PAR_DEFAUT_CLASSEMENT
    lim = _limite(limite)

    qs = _operations_periode(entreprise, periode, date_debut, date_fin).filter(
        transaction_type=type_operation
    )
    total_periode = _somme(qs)
    rattachees = qs.filter(contact__isnull=False)
    total_rattache = _somme(rattachees)

    lignes = (
        rattachees.values("contact__nom")
        .annotate(total=Sum("amount_ttc"), nombre=Count("id"))
        .order_by("-total", "contact__nom")[:lim]
    )
    classement = [
        {
            "rang": rang,
            "nom": ligne["contact__nom"],
            "total": _montant(entreprise, ligne["total"]),
            "nombre_operations": ligne["nombre"],
        }
        for rang, ligne in enumerate(lignes, start=1)
    ]

    return {
        "success": True,
        "periode": _label_periode(periode),
        "classement": classement,
        "total_periode": _montant(entreprise, total_periode),
        "dont_sans_contact_renseigne": _montant(
            entreprise, total_periode - total_rattache
        ),
    }


def get_top_clients(
    entreprise, periode=None, limite=LIMITE_PAR_DEFAUT,
    date_debut=None, date_fin=None, **_ignored
):
    """Meilleurs clients : ventes (RECETTE) rattachées à un contact, par total."""
    return _classement_contacts(
        entreprise, "RECETTE", periode, limite, date_debut, date_fin
    )


def get_top_suppliers(
    entreprise, periode=None, limite=LIMITE_PAR_DEFAUT,
    date_debut=None, date_fin=None, **_ignored
):
    """Principaux fournisseurs : dépenses (DEPENSE) rattachées à un contact, par total."""
    return _classement_contacts(
        entreprise, "DEPENSE", periode, limite, date_debut, date_fin
    )


def get_top_categories(
    entreprise, type_operation="RECETTE", periode=None, limite=LIMITE_PAR_DEFAUT,
    date_debut=None, date_fin=None, **_ignored
):
    """Ce qui rapporte (RECETTE) ou coûte (DEPENSE) le plus, par catégorie."""
    type_operation = (
        type_operation if type_operation in ("RECETTE", "DEPENSE") else "RECETTE"
    )
    periode = periode or PERIODE_PAR_DEFAUT_CLASSEMENT
    lim = _limite(limite)

    qs = _operations_periode(entreprise, periode, date_debut, date_fin).filter(
        transaction_type=type_operation
    )
    lignes = qs.values("category").annotate(total=Sum("amount_ttc"), nombre=Count("id"))

    # NULL et chaîne vide forment deux groupes SQL distincts : on les fusionne.
    regroupees: dict[str, dict] = {}
    for ligne in lignes:
        nom = _texte(ligne["category"]) or SANS_CATEGORIE
        cible = regroupees.setdefault(nom, {"total": Decimal("0"), "nombre": 0})
        cible["total"] += ligne["total"] or Decimal("0")
        cible["nombre"] += ligne["nombre"]

    triees = sorted(regroupees.items(), key=lambda kv: (-kv[1]["total"], kv[0]))[:lim]
    return {
        "success": True,
        "type_operation": type_operation,
        "periode": _label_periode(periode),
        "classement": [
            {
                "rang": rang,
                "categorie": nom,
                "total": _montant(entreprise, donnees["total"]),
                "nombre_operations": donnees["nombre"],
            }
            for rang, (nom, donnees) in enumerate(triees, start=1)
        ],
        "total_periode": _montant(entreprise, _somme(qs)),
    }


def list_prestations(entreprise, **_ignored):
    """Services / prestations actifs de l'entreprise, avec leurs prix."""
    qs = Prestation.objects.filter(entreprise=entreprise, actif=True).order_by("nom")
    nombre_total = qs.count()
    prestations = []
    for p in qs[:30]:
        prestations.append(
            {
                "nom": p.nom,
                "prix_unitaire": _montant(entreprise, p.prix_unitaire),
                "cout_unitaire": (
                    _montant(entreprise, p.cout_unitaire)
                    if p.cout_unitaire is not None
                    else None
                ),
                "duree_estimee_minutes": p.duree_estimee_minutes,
            }
        )
    return {
        "success": True,
        "nombre_total": nombre_total,
        "prestations": prestations,
    }


def search_operations(
    entreprise, mot_cle=None, contact=None, transaction_type=None, periode=None,
    limite=10, date_debut=None, date_fin=None, **_ignored
):
    """
    Recherche en lecture seule dans les opérations enregistrées.

    - mot_cle : cherché dans description, catégorie, nom du contact et champ
      vendor_or_client (insensible à la casse).
    - contact : nom (ou morceau de nom) du contact.
    - periode absente : tout l'historique.
    Renvoie les `limite` opérations les plus récentes, plus les totaux sur
    TOUTES les opérations trouvées (pas seulement celles affichées).
    """
    lim = _limite(limite, defaut=10)

    if periode:
        qs = _operations_periode(entreprise, periode, date_debut, date_fin)
    else:
        qs = Operation.objects.filter(entreprise=entreprise)

    if transaction_type in _TYPES_OPERATION:
        qs = qs.filter(transaction_type=transaction_type)

    mot = _texte(mot_cle, MOT_CLE_MAX_CHARS)
    if mot:
        qs = qs.filter(
            Q(description__icontains=mot)
            | Q(category__icontains=mot)
            | Q(vendor_or_client__icontains=mot)
            | Q(contact__nom__icontains=mot)
        )

    nom_contact = _texte(contact, MOT_CLE_MAX_CHARS)
    if nom_contact:
        qs = qs.filter(
            Q(contact__nom__icontains=nom_contact)
            | Q(vendor_or_client__icontains=nom_contact)
        )

    nombre_trouve = qs.count()
    totaux = [
        {
            "type": ligne["transaction_type"],
            "nombre": ligne["nombre"],
            "total": _montant(entreprise, ligne["total"]),
        }
        for ligne in qs.values("transaction_type")
        .annotate(total=Sum("amount_ttc"), nombre=Count("id"))
        .order_by("transaction_type")
    ]

    operations = []
    for op in qs.select_related("contact").order_by("-transaction_date", "-created_at")[:lim]:
        reste = op.amount_ttc - op.montant_paye
        operations.append(
            {
                "date": op.transaction_date.isoformat(),
                "type": op.transaction_type,
                "montant": _montant(entreprise, op.amount_ttc),
                "contact": op.contact.nom if op.contact_id else op.vendor_or_client,
                "description": _texte(op.description, 200),
                "categorie": op.category,
                "statut_paiement": op.statut_paiement,
                "reste_a_payer": (
                    _montant(entreprise, reste) if op.statut_paiement == "CREDIT" else None
                ),
            }
        )

    return {
        "success": True,
        "periode": _label_periode(periode) if periode else "tout l'historique",
        "nombre_operations_trouvees": nombre_trouve,
        "operations_affichees": len(operations),
        "totaux_par_type": totaux,
        "operations": operations,
    }