import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.femi_account.models import Utilisateur

CODE_VALIDITE_HEURES = 48
CODE_ESSAIS_MAX = 5


def generer_code_activation():
    """Code à 6 chiffres, tiré avec un générateur cryptographique."""
    return f"{secrets.randbelow(10 ** 6):06d}"


class EstAdminEntreprise(BasePermission):
    """Autorise uniquement un ADMIN rattaché à une entreprise."""

    message = "Action réservée à l'administrateur de l'entreprise."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.entreprise_id
            and (user.role or "").strip().upper() == "ADMIN"
        )


class CreerUtilisateurEntrepriseAPIView(APIView):
    """
    POST : crée un employé dans l'entreprise de l'administrateur connecté.

    Champs : nom_complet (requis), email (requis), poste (facultatif).
    Le rôle est forcé à EMPLOYE et l'entreprise vient du token, jamais de
    la requête. Le compte est créé sans mot de passe utilisable : l'employé
    choisira le sien avec le code d'activation renvoyé ici UNE SEULE FOIS.
    """

    permission_classes = [IsAuthenticated, EstAdminEntreprise]

    def post(self, request, *args, **kwargs):
        admin = request.user

        nom_complet = (request.data.get("nom_complet") or "").strip()
        email = (request.data.get("email") or "").strip().lower()
        poste = (request.data.get("poste") or "").strip() or None

        if not nom_complet or not email:
            return Response(
                {"error": "nom_complet et email sont requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_email(email)
        except DjangoValidationError:
            return Response(
                {"error": "Adresse e-mail invalide."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(email) > 150 or len(nom_complet) > 255 or (poste and len(poste) > 100):
            return Response(
                {"error": "Un des champs est trop long."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Utilisateur.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists():
            return Response(
                {"error": "Cette adresse e-mail est déjà utilisée."},
                status=status.HTTP_409_CONFLICT,
            )

        code = generer_code_activation()
        expire = timezone.now() + timedelta(hours=CODE_VALIDITE_HEURES)
        parts = nom_complet.split(" ", 1)

        user = Utilisateur(
            username=email,
            email=email,
            entreprise_id=admin.entreprise_id,
            role="EMPLOYE",
            poste=poste,
            full_name=nom_complet,
            first_name=parts[0][:150],
            last_name=(parts[1] if len(parts) > 1 else "")[:150],
            code_activation_hash=make_password(code),
            code_activation_expire=expire,
            code_activation_essais=0,
        )
        user.set_unusable_password()

        try:
            user.save()
        except IntegrityError:
            return Response(
                {"error": "Cette adresse e-mail est déjà utilisée."},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                "utilisateur_id": str(user.id),
                "identifiant": user.email,
                "nom_complet": user.full_name,
                "poste": user.poste,
                "role": user.role,
                "code_activation": code,
                "code_expire": expire.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


def _refus():
    # Message volontairement identique pour tous les échecs : on ne révèle
    # pas si l'e-mail existe, si le code est faux ou s'il a expiré.
    return Response(
        {"error": "Code invalide ou expiré."},
        status=status.HTTP_400_BAD_REQUEST,
    )


class ActiverCompteAPIView(APIView):
    """
    POST (public) : l'employé active son compte avec son e-mail et le code
    reçu, et choisit son mot de passe. Renvoie un token comme la connexion.

    Champs : email, code, password.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        email = (request.data.get("email") or "").strip().lower()
        code = str(request.data.get("code") or "").strip()
        password = request.data.get("password") or ""

        if not email or not code or not password:
            return Response(
                {"error": "email, code et password sont requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            user = (
                Utilisateur.objects.select_for_update()
                .filter(email__iexact=email, role__iexact="EMPLOYE", is_active=True)
                .first()
            )

            if (
                user is None
                or not user.code_activation_hash
                or not user.code_activation_expire
                or user.code_activation_expire < timezone.now()
                or user.code_activation_essais >= CODE_ESSAIS_MAX
            ):
                return _refus()

            if not check_password(code, user.code_activation_hash):
                user.code_activation_essais += 1
                user.save(update_fields=["code_activation_essais"])
                return _refus()

            try:
                validate_password(password, user)
            except DjangoValidationError as exc:
                return Response(
                    {"error": " ".join(exc.messages)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user.set_password(password)
            user.code_activation_hash = None
            user.code_activation_expire = None
            user.code_activation_essais = 0
            user.save(update_fields=[
                "password",
                "code_activation_hash",
                "code_activation_expire",
                "code_activation_essais",
            ])

        token, _ = Token.objects.get_or_create(user=user)
        entreprise = user.entreprise

        return Response(
            {
                "token": token.key,
                "utilisateur_id": str(user.id),
                "role": user.role,
                "entreprise_id": str(entreprise.id) if entreprise else None,
                "entreprise_nom": entreprise.nom if entreprise else None,
            },
            status=status.HTTP_200_OK,
        )


class RegenererCodeActivationAPIView(APIView):
    """
    POST : l'administrateur génère un nouveau code d'activation pour un
    employé de son entreprise qui n'a pas encore activé son compte.
    L'ancien code est invalidé et le compteur d'essais remis à zéro.
    """

    permission_classes = [IsAuthenticated, EstAdminEntreprise]

    def post(self, request, utilisateur_id, *args, **kwargs):
        employe = Utilisateur.objects.filter(
            id=utilisateur_id,
            entreprise_id=request.user.entreprise_id,
            role__iexact="EMPLOYE",
            is_active=True,
        ).first()

        if employe is None:
            return Response(
                {"error": "Employé introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if employe.has_usable_password():
            return Response(
                {"error": "Ce compte est déjà activé."},
                status=status.HTTP_409_CONFLICT,
            )

        code = generer_code_activation()
        expire = timezone.now() + timedelta(hours=CODE_VALIDITE_HEURES)

        employe.code_activation_hash = make_password(code)
        employe.code_activation_expire = expire
        employe.code_activation_essais = 0
        employe.save(update_fields=[
            "code_activation_hash",
            "code_activation_expire",
            "code_activation_essais",
        ])

        return Response(
            {
                "utilisateur_id": str(employe.id),
                "identifiant": employe.email,
                "code_activation": code,
                "code_expire": expire.isoformat(),
            },
            status=status.HTTP_200_OK,
        )


from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce


class EquipeAPIView(APIView):
    """
    GET : liste des employés de l'entreprise de l'administrateur connecté,
    avec leurs ventes du jour (opérations RECETTE datées d'aujourd'hui).
    """

    permission_classes = [IsAuthenticated, EstAdminEntreprise]

    def get(self, request, *args, **kwargs):
        aujourdhui = timezone.localdate()

        employes = (
            Utilisateur.objects.filter(
                entreprise_id=request.user.entreprise_id,
                role__iexact="EMPLOYE",
            )
            .annotate(
                ventes_jour=Coalesce(
                    Sum(
                        "operations__amount_ttc",
                        filter=Q(
                            operations__transaction_type__iexact="RECETTE",
                            operations__transaction_date=aujourdhui,
                        ),
                    ),
                    Decimal("0.00"),
                )
            )
            .order_by("full_name", "email")
        )

        return Response(
            {
                "total": len(employes),
                "employes": [
                    {
                        "utilisateur_id": str(e.id),
                        "nom_complet": e.full_name,
                        "email": e.email,
                        "poste": e.poste,
                        "actif": e.is_active,
                        "compte_active": e.has_usable_password(),
                        "ventes_jour": float(e.ventes_jour),
                    }
                    for e in employes
                ],
            },
            status=status.HTTP_200_OK,
        )


from apps.femi_account.models import Operation


class FicheEmployeAPIView(APIView):
    """
    GET : fiche d'un employé de l'entreprise de l'administrateur connecté,
    avec ses ventes (RECETTE) aujourd'hui, cette semaine (depuis le lundi)
    et ce mois (depuis le 1er).
    """

    permission_classes = [IsAuthenticated, EstAdminEntreprise]

    def get(self, request, utilisateur_id, *args, **kwargs):
        entreprise = request.user.entreprise

        employe = Utilisateur.objects.filter(
            id=utilisateur_id,
            entreprise_id=request.user.entreprise_id,
            role__iexact="EMPLOYE",
        ).first()

        if employe is None:
            return Response(
                {"error": "Employé introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )

        aujourdhui = timezone.localdate()
        debut_semaine = aujourdhui - timedelta(days=aujourdhui.weekday())
        debut_mois = aujourdhui.replace(day=1)
        zero = Decimal("0.00")

        totaux = Operation.objects.filter(
            entreprise_id=request.user.entreprise_id,
            utilisateur=employe,
            transaction_type__iexact="RECETTE",
        ).aggregate(
            aujourdhui=Coalesce(
                Sum("amount_ttc", filter=Q(transaction_date=aujourdhui)), zero
            ),
            semaine=Coalesce(
                Sum(
                    "amount_ttc",
                    filter=Q(
                        transaction_date__gte=debut_semaine,
                        transaction_date__lte=aujourdhui,
                    ),
                ),
                zero,
            ),
            mois=Coalesce(
                Sum(
                    "amount_ttc",
                    filter=Q(
                        transaction_date__gte=debut_mois,
                        transaction_date__lte=aujourdhui,
                    ),
                ),
                zero,
            ),
        )

        return Response(
            {
                "utilisateur_id": str(employe.id),
                "nom_complet": employe.full_name,
                "email": employe.email,
                "poste": employe.poste,
                "actif": employe.is_active,
                "compte_active": employe.has_usable_password(),
                "devise": (entreprise.devise if entreprise else None) or "XOF",
                "ventes": {
                    "aujourdhui": float(totaux["aujourdhui"]),
                    "semaine": float(totaux["semaine"]),
                    "mois": float(totaux["mois"]),
                },
            },
            status=status.HTTP_200_OK,
        )


class StatutEmployeAPIView(APIView):
    """
    PATCH : active ou désactive un employé de l'entreprise de
    l'administrateur connecté. Corps : {"actif": true|false}.
    La désactivation supprime son token (déconnexion immédiate) et ne
    supprime aucune donnée.
    """

    permission_classes = [IsAuthenticated, EstAdminEntreprise]

    def patch(self, request, utilisateur_id, *args, **kwargs):
        actif = request.data.get("actif")

        if not isinstance(actif, bool):
            return Response(
                {"error": "Le champ actif doit être true ou false."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        employe = Utilisateur.objects.filter(
            id=utilisateur_id,
            entreprise_id=request.user.entreprise_id,
            role__iexact="EMPLOYE",
        ).first()

        if employe is None:
            return Response(
                {"error": "Employé introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )

        employe.is_active = actif
        employe.save(update_fields=["is_active"])

        if not actif:
            Token.objects.filter(user=employe).delete()

        return Response(
            {"utilisateur_id": str(employe.id), "actif": employe.is_active},
            status=status.HTTP_200_OK,
        )
