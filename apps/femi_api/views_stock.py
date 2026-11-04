import logging
from datetime import datetime, time

from django.db import transaction
from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.femi_account.devis_service import utilisateur_est_employe
from apps.femi_account.models import MouvementStock, Produit
from apps.femi_account.stock_import import (
    ImportInvalideError,
    lire_fichier_produits,
    modele_xlsx,
)
from apps.femi_account.stock_service import (
    MouvementInvalideError,
    ProduitIntrouvableError,
    StockError,
    StockInsuffisantError,
    enregistrer_mouvement,
    resume_stock,
)
from apps.femi_api.serializers_stock import (
    MouvementEcritureSerializer,
    MouvementSerializer,
    ProduitEcritureSerializer,
    ProduitSerializer,
)

logger = logging.getLogger(__name__)

MESSAGE_ADMIN_SEULEMENT = "Cette action est réservée à l'administrateur de l'entreprise."
LIMITE_PAR_DEFAUT = 100
LIMITE_MAX = 500


def _entreprise_ou_erreur(request):
    entreprise = getattr(request.user, "entreprise", None)
    if entreprise is None:
        return None, Response(
            {"error": "Aucune entreprise associée à ce compte."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return entreprise, None


def _refus_employe():
    return Response({"error": MESSAGE_ADMIN_SEULEMENT}, status=status.HTTP_403_FORBIDDEN)


def _creer_produit_avec_stock(entreprise, donnees, utilisateur):
    """Crée le produit puis, si un stock de départ est donné, son mouvement initial.

    `donnees` vient du serializer validé. Lève StockError ; à appeler dans une
    transaction pour annuler le produit si le mouvement échoue.
    """
    donnees = dict(donnees)
    stock_initial = donnees.pop("stock_initial", None)
    cout_initial = donnees.pop("cout_initial", None)
    produit = Produit.objects.create(entreprise=entreprise, **donnees)
    if stock_initial:
        enregistrer_mouvement(
            entreprise=entreprise,
            produit_id=produit.pk,
            type_mouvement="ENTREE",
            quantite=stock_initial,
            motif="STOCK_INITIAL",
            cout_unitaire=cout_initial,
            utilisateur=utilisateur,
        )
    produit.refresh_from_db()
    return produit


def _message_erreurs(erreurs) -> str:
    """Aplatit les erreurs d'un serializer DRF en une phrase lisible."""
    morceaux = []
    for champ, messages in erreurs.items():
        texte = " ".join(str(m) for m in (messages if isinstance(messages, list) else [messages]))
        morceaux.append(texte if champ == "non_field_errors" else f"{champ} : {texte}")
    return " ; ".join(morceaux)


def _entier(valeur, defaut, minimum=0, maximum=None):
    try:
        n = int(valeur)
    except (TypeError, ValueError):
        return defaut
    n = max(n, minimum)
    return min(n, maximum) if maximum is not None else n


class ResumeStockAPIView(APIView):
    """GET : chiffres clés (nombre de produits, valeur du stock, alertes, ruptures)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        return Response(resume_stock(entreprise))


class ProduitListCreateAPIView(APIView):
    """GET : liste des produits. POST : crée un produit (administrateur)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur

        qs = Produit.objects.filter(entreprise=entreprise)

        actif = (request.query_params.get("actif") or "true").lower()
        if actif == "true":
            qs = qs.filter(actif=True)
        elif actif == "false":
            qs = qs.filter(actif=False)  # produits archivés ; « all » : tous

        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(nom__icontains=q) | Q(sku__icontains=q))

        statut = (request.query_params.get("statut") or "").upper()
        if statut == "RUPTURE":
            qs = qs.filter(stock_actuel__lte=0)
        elif statut == "ALERTE":
            qs = qs.filter(
                stock_actuel__gt=0, seuil_alerte__gt=0, stock_actuel__lte=F("seuil_alerte")
            )

        limite = _entier(request.query_params.get("limit"), LIMITE_MAX, 1, LIMITE_MAX)
        decalage = _entier(request.query_params.get("offset"), 0)
        total = qs.count()
        produits = qs[decalage:decalage + limite]
        return Response(
            {"total": total, "resultats": ProduitSerializer(produits, many=True).data}
        )

    def post(self, request):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        if utilisateur_est_employe(request.user):
            return _refus_employe()

        serializer = ProduitEcritureSerializer(
            data=request.data, context={"entreprise": entreprise}
        )
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                produit = _creer_produit_avec_stock(
                    entreprise, serializer.validated_data, request.user
                )
        except StockError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ProduitSerializer(produit).data, status=status.HTTP_201_CREATED)


_TAILLE_MAX_IMPORT = 2 * 1024 * 1024  # 2 Mo


class ImportProduitsAPIView(APIView):
    """POST (multipart, champ « fichier ») : importe des produits depuis un .xlsx.

    Réservé à l'administrateur. Chaque ligne est traitée séparément : les lignes
    valides sont créées, les autres sont listées avec leur motif (numéro de ligne
    Excel). Un produit déjà existant (même nom) est ignoré, jamais écrasé.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        if utilisateur_est_employe(request.user):
            return _refus_employe()

        fichier = request.FILES.get("fichier")
        if fichier is None:
            return Response(
                {"error": "Fichier « fichier » manquant."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if fichier.size > _TAILLE_MAX_IMPORT:
            return Response(
                {"error": "Fichier trop volumineux (2 Mo maximum)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            lignes = lire_fichier_produits(fichier)
        except ImportInvalideError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        crees = 0
        ignores = []
        noms_vus = set()
        for ligne in lignes:
            donnees = ligne["donnees"]
            nom = (donnees or {}).get("nom", "")
            if ligne["erreur"]:
                ignores.append({"ligne": ligne["ligne"], "nom": nom, "erreur": ligne["erreur"]})
                continue
            if nom.strip().lower() in noms_vus:
                ignores.append(
                    {"ligne": ligne["ligne"], "nom": nom, "erreur": "Doublon dans le fichier."}
                )
                continue

            serializer = ProduitEcritureSerializer(
                data=donnees, context={"entreprise": entreprise}
            )
            if not serializer.is_valid():
                ignores.append(
                    {
                        "ligne": ligne["ligne"],
                        "nom": nom,
                        "erreur": _message_erreurs(serializer.errors),
                    }
                )
                continue
            try:
                with transaction.atomic():
                    _creer_produit_avec_stock(entreprise, serializer.validated_data, request.user)
            except StockError as exc:
                ignores.append({"ligne": ligne["ligne"], "nom": nom, "erreur": str(exc)})
                continue
            noms_vus.add(nom.strip().lower())
            crees += 1

        return Response({"total_lignes": len(lignes), "crees": crees, "ignores": ignores})


class ModeleImportProduitsAPIView(APIView):
    """GET : télécharge le fichier Excel modèle pour l'import de produits."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        reponse = HttpResponse(
            modele_xlsx(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        reponse["Content-Disposition"] = 'attachment; filename="modele_import_produits.xlsx"'
        return reponse


class ProduitDetailAPIView(APIView):
    """GET : un produit. PATCH : modifie sa fiche (administrateur).

    Pas de DELETE : on archive avec `actif = false` pour garder l'historique.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        produit = get_object_or_404(Produit, pk=pk, entreprise=entreprise)
        return Response(ProduitSerializer(produit).data)

    def patch(self, request, pk):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur
        if utilisateur_est_employe(request.user):
            return _refus_employe()
        produit = get_object_or_404(Produit, pk=pk, entreprise=entreprise)
        serializer = ProduitEcritureSerializer(
            produit, data=request.data, partial=True, context={"entreprise": entreprise}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        produit.refresh_from_db()
        return Response(ProduitSerializer(produit).data)


class MouvementListCreateAPIView(APIView):
    """GET : historique des mouvements. POST : enregistre un mouvement.

    Un employé ne peut enregistrer que des SORTIES pour VENTE ; les entrées,
    pertes et ajustements d'inventaire sont réservés à l'administrateur.
    L'en-tête `Idempotency-Key` (ou le champ `cle_idempotence`) protège contre
    les envois en double : le mouvement déjà enregistré est alors renvoyé (200).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur

        qs = MouvementStock.objects.filter(entreprise=entreprise).select_related(
            "produit", "utilisateur"
        )
        params = request.query_params
        if params.get("produit"):
            qs = qs.filter(produit_id=params["produit"])
        if params.get("type"):
            qs = qs.filter(type_mouvement=params["type"].upper())
        for cle, borne, heure in (("date_debut", "gte", time.min), ("date_fin", "lte", time.max)):
            texte = params.get(cle)
            if not texte:
                continue
            try:
                jour = datetime.strptime(texte, "%Y-%m-%d").date()
            except ValueError:
                return Response(
                    {"error": f"{cle} invalide (format AAAA-MM-JJ)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            moment = timezone.make_aware(datetime.combine(jour, heure))
            qs = qs.filter(**{f"date_mouvement__{borne}": moment})

        limite = _entier(params.get("limit"), LIMITE_PAR_DEFAUT, 1, LIMITE_MAX)
        decalage = _entier(params.get("offset"), 0)
        total = qs.count()
        return Response(
            {
                "total": total,
                "resultats": MouvementSerializer(qs[decalage:decalage + limite], many=True).data,
            }
        )

    def post(self, request):
        entreprise, erreur = _entreprise_ou_erreur(request)
        if erreur:
            return erreur

        serializer = MouvementEcritureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        motif = (d.get("motif") or "").strip().upper() or None
        if utilisateur_est_employe(request.user):
            if d["type_mouvement"] != "SORTIE" or (motif or "VENTE") != "VENTE":
                return Response(
                    {"error": "Un employé ne peut enregistrer que des ventes (sorties de stock)."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        cle = (
            (d.get("cle_idempotence") or "").strip()
            or (request.headers.get("Idempotency-Key") or "").strip()
            or None
        )
        if cle:
            cle = cle[:64]

        try:
            mouvement, cree = enregistrer_mouvement(
                entreprise=entreprise,
                produit_id=d["produit"],
                type_mouvement=d["type_mouvement"],
                quantite=d["quantite"],
                motif=motif,
                cout_unitaire=d.get("cout_unitaire"),
                note=d.get("note", ""),
                utilisateur=request.user,
                cle_idempotence=cle,
            )
        except ProduitIntrouvableError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        except StockInsuffisantError as exc:
            return Response(
                {"error": str(exc), "code": "STOCK_INSUFFISANT"},
                status=status.HTTP_409_CONFLICT,
            )
        except MouvementInvalideError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        mouvement = MouvementStock.objects.select_related("produit", "utilisateur").get(
            pk=mouvement.pk
        )
        corps = {
            "mouvement": MouvementSerializer(mouvement).data,
            "produit": ProduitSerializer(mouvement.produit).data,
        }
        return Response(corps, status=status.HTTP_201_CREATED if cree else status.HTTP_200_OK)


class StockOcrPreviewAPIView(APIView):
    """POST multipart (champ `image`) : OCR stock sans écriture en base.

    Réponse destinée au mobile pour préremplir le formulaire produit/mouvement.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        entreprise, err = _entreprise_ou_erreur(request)
        if err:
            return err

        fichier = request.FILES.get("image") or request.FILES.get("fichier")
        if fichier is None:
            return Response(
                {"error": "Envoie une image (champ « image »)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw = fichier.read()
        if not raw:
            return Response(
                {"error": "Fichier vide."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from apps.femi_agent.agent.stock_ocr_executor import (
                StockOcrExecutionError,
                StockOcrExecutor,
            )
            from apps.femi_agent.parsers.document_loader import UnsupportedDocumentError

            result = StockOcrExecutor.execute(raw)
        except UnsupportedDocumentError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("[StockOcrPreview] échec OCR")
            return Response(
                {"error": "Impossible de lire l'image. Réessaie avec une photo plus nette."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        data = result.model_dump()
        # Enrichir : produit existant ?
        from apps.femi_account.models import Produit

        for ligne in data.get("lignes") or []:
            nom = (ligne.get("nom") or "").strip()
            sku = (ligne.get("sku") or "").strip()
            existant = None
            if sku:
                existant = Produit.objects.filter(
                    entreprise=entreprise, sku__iexact=sku, actif=True
                ).first()
            if existant is None and nom:
                existant = Produit.objects.filter(
                    entreprise=entreprise, nom__iexact=nom, actif=True
                ).first()
            ligne["produit_existant_id"] = str(existant.pk) if existant else None
            ligne["produit_existant_nom"] = existant.nom if existant else None
            ligne["stock_actuel"] = float(existant.stock_actuel) if existant else None

        data["message"] = (
            "Champs proposés à partir de la photo. Vérifie puis valide le formulaire."
        )
        return Response(data, status=status.HTTP_200_OK)
