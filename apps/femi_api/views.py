from django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from django.utils import timezone
from django.db.models import Sum, Avg, Q
from decimal import Decimal

# Importations DRF-Spectacular pour Swagger OAS 3.0
try:
    from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
    HAS_SPECTACULAR = True
except ImportError:
    HAS_SPECTACULAR = False

from apps.femi_api.serializers import (
    TransactionPayloadSerializer,
    OperationModelSerializer,
    BatchTransactionPayloadSerializer,
)
from apps.femi_agent.agent.manager import FemiAgentManager
from apps.femi_account.models import Kpi, Niveau, Contact
from apps.femi_account.models import Operation, Entreprise, PrestationRealisee, Utilisateur, WhatsAppLinkRequest
from apps.femi_whatsapp.whatsapp_client import WhatsAppClient
from apps.femi_whatsapp.tasks import _normalize_phone


class ProcessTransactionAPIView(APIView):
    """
    1. ENDPOINT UNIFIÉ (POST)
    Gère la réception des transactions (Texte, Image, Audio)
    et répond aux questions de l'utilisateur.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    serializer_class = TransactionPayloadSerializer

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Traiter une entrée (Texte, Audio, Image)",
            description="Reçoit une saisie utilisateur, l'analyse via l'agent et enregistre la transaction ou renvoie une réponse.",
            request=TransactionPayloadSerializer,
            responses={
                201: OperationModelSerializer,
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT,
                500: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        serializer = TransactionPayloadSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )

        validated_data = serializer.validated_data

        text = validated_data.get('text')
        image_file = validated_data.get('image')
        audio_file = validated_data.get('audio')
        source = validated_data.get('source', 'MOBILE')

        image_bytes = image_file.read() if image_file else None
        audio_bytes = audio_file.read() if audio_file else None

        result = FemiAgentManager.process_transaction_text(
            text_input=text,
            image_bytes=image_bytes,
            audio_bytes=audio_bytes,
            image_file=image_file,
            source=source,
            entreprise_id=(
                request.user.entreprise.id
                if request.user.entreprise
                else None
            ),
            utilisateur_id=request.user.id
        )

        if not result.success:
            return Response(
                {"error": result.message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Question analytique ou bilan
        if result.operation_instance is None:
            return Response(
                {"message": result.message},
                status=status.HTTP_200_OK
            )

        # Transaction enregistrée
        response_data = OperationModelSerializer(
            result.operation_instance
        ).data

        response_data["message"] = result.message

        return Response(
            response_data,
            status=status.HTTP_201_CREATED
        )


class DashboardKPIAPIView(APIView):
    """
    2. ENDPOINT KPIS DASHBOARD (GET)
    Fournit les indicateurs financiers du tableau de bord.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Récupérer les KPIs du Dashboard",
            description="Renvoie les métriques financières agrégées.",
            parameters=[
                OpenApiParameter(
                    name='period',
                    type=OpenApiTypes.STR,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    default='this_month',
                    description="Période d'analyse des métriques",
                    enum=[
                        'today',
                        'this_month',
                        'last_month',
                        'this_year'
                    ]
                ),
            ],
            responses={200: OpenApiTypes.OBJECT}
        )
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)
    else:
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)

    def _handle_get(self, request, *args, **kwargs):

        period = request.query_params.get(
            'period',
            'this_month'
        )

        entreprise = request.user.entreprise

        if not entreprise:
            return Response(
                {"error": "Aucune entreprise configurée"},
                status=status.HTTP_404_NOT_FOUND
            )

        now = timezone.now()

        ops = Operation.objects.filter(
            entreprise=entreprise
        )

        # Filtre par période
        if period == 'today':

            ops = ops.filter(
                transaction_date=now.date()
            )

        elif period == 'this_month':

            ops = ops.filter(
                transaction_date__year=now.year,
                transaction_date__month=now.month
            )

        elif period == 'last_month':

            month = 12 if now.month == 1 else now.month - 1
            year = now.year - 1 if now.month == 1 else now.year

            ops = ops.filter(
                transaction_date__year=year,
                transaction_date__month=month
            )

        elif period == 'this_year':

            ops = ops.filter(
                transaction_date__year=now.year
            )

        # KPIs
        revenue = (
            ops
            .filter(transaction_type="RECETTE")
            .aggregate(t=Sum('amount_ttc'))['t']
            or Decimal('0.00')
        )

        expenses = (
            ops
            .filter(transaction_type="DEPENSE")
            .aggregate(t=Sum('amount_ttc'))['t']
            or Decimal('0.00')
        )

        avg_sale = (
            ops
            .filter(transaction_type="RECETTE")
            .aggregate(a=Avg('amount_ttc'))['a']
            or Decimal('0.00')
        )

        profit = revenue - expenses

        margin = (
            profit / revenue * 100
            if revenue > 0
            else Decimal('0.00')
        )

        # Nombre de clients uniques
        unique_clients_count = (
            ops
            .filter(transaction_type="RECETTE")
            .exclude(vendor_or_client__isnull=True)
            .exclude(vendor_or_client__exact='')
            .values('vendor_or_client')
            .distinct()
            .count()
        )

        # Top 3 catégories de dépenses
        top_categories = (
            ops
            .filter(transaction_type="DEPENSE")
            .values('category')
            .annotate(amount=Sum('amount_ttc'))
            .order_by('-amount')[:3]
        )

        # 5 transactions récentes
        recent_ops = (
            ops
            .order_by('-created_at')[:5]
            .values(
                'id',
                'transaction_type',
                'amount_ttc',
                'category',
                'transaction_date'
            )
        )

        # Tendances : période précédente
        prev_ops = self._get_previous_period_ops(
            entreprise,
            period,
            now
        )

        prev_revenue = (
            prev_ops
            .filter(transaction_type="RECETTE")
            .aggregate(t=Sum('amount_ttc'))['t']
            or Decimal('0.00')
        )

        prev_expenses = (
            prev_ops
            .filter(transaction_type="DEPENSE")
            .aggregate(t=Sum('amount_ttc'))['t']
            or Decimal('0.00')
        )

        prev_profit = prev_revenue - prev_expenses

        # Trésorerie globale
        all_ops = Operation.objects.filter(
            entreprise=entreprise
        )

        total_in = (
            all_ops
            .filter(transaction_type="RECETTE")
            .aggregate(t=Sum('amount_ttc'))['t']
            or Decimal('0.00')
        )

        total_out = (
            all_ops
            .filter(transaction_type="DEPENSE")
            .aggregate(t=Sum('amount_ttc'))['t']
            or Decimal('0.00')
        )

        treasury_balance = total_in - total_out

        return Response(
            {
                "period": period,

                "currency": entreprise.devise or "XOF",

                "kpis": {
                    "total_revenue": float(revenue),
                    "total_expenses": float(expenses),
                    "net_profit": float(profit),
                    "profit_margin_percentage": round(
                        float(margin),
                        2
                    ),
                    "average_sale_amount": round(
                        float(avg_sale),
                        2
                    ),
                    "total_transactions_count": ops.count(),
                    "unique_clients_count": unique_clients_count,
                },

                "trends": {
                    "revenue_change_percentage": self._pct_change(
                        revenue,
                        prev_revenue
                    ),
                    "expenses_change_percentage": self._pct_change(
                        expenses,
                        prev_expenses
                    ),
                    "profit_change_percentage": self._pct_change(
                        profit,
                        prev_profit
                    ),
                },

                "treasury": {
                    "balance": float(treasury_balance),
                    "as_of": now.isoformat()
                },

                "top_expense_categories": list(
                    top_categories
                ),

                "recent_transactions": list(
                    recent_ops
                )
            },
            status=status.HTTP_200_OK
        )

    def _get_previous_period_ops(
        self,
        entreprise,
        period,
        now
    ):
        """
        Retourne les opérations de la période précédente.
        """

        ops = Operation.objects.filter(
            entreprise=entreprise
        )

        if period == 'today':

            yesterday = (
                now.date()
                - timezone.timedelta(days=1)
            )

            return ops.filter(
                transaction_date=yesterday
            )

        elif period == 'this_month':

            month = 12 if now.month == 1 else now.month - 1

            year = (
                now.year - 1
                if now.month == 1
                else now.year
            )

            return ops.filter(
                transaction_date__year=year,
                transaction_date__month=month
            )

        elif period == 'last_month':

            ref_month = (
                12
                if now.month == 1
                else now.month - 1
            )

            ref_year = (
                now.year - 1
                if now.month == 1
                else now.year
            )

            month = (
                12
                if ref_month == 1
                else ref_month - 1
            )

            year = (
                ref_year - 1
                if ref_month == 1
                else ref_year
            )

            return ops.filter(
                transaction_date__year=year,
                transaction_date__month=month
            )

        elif period == 'this_year':

            return ops.filter(
                transaction_date__year=now.year - 1
            )

        return ops.none()

    def _pct_change(self, current, previous):
        """
        Calcule le pourcentage de variation.
        """

        if previous == 0:

            return (
                None
                if current == 0
                else 100.0
            )

        return round(
            float(
                (current - previous)
                / previous
                * 100
            ),
            2
        )


class HealthCheckAPIView(APIView):
    """
    3. ENDPOINT HEALTHCHECK (GET)
    Vérifie que l'API et la base de données répondent.
    """

    authentication_classes = []
    permission_classes = []

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Vérifier l'état de l'API",
            description="Endpoint public de healthcheck.",
            responses={
                200: OpenApiTypes.OBJECT
            }
        )
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)
    else:
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)

    def _handle_get(self, request, *args, **kwargs):

        from django.db import connection

        db_status = "ok"

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")

        except Exception as e:
            db_status = f"error: {str(e)}"

        overall_ok = db_status == "ok"

        return Response(
            {
                "status": (
                    "ok"
                    if overall_ok
                    else "degraded"
                ),
                "database": db_status,
                "timestamp": timezone.now().isoformat()
            },
            status=(
                status.HTTP_200_OK
                if overall_ok
                else status.HTTP_503_SERVICE_UNAVAILABLE
            )
        )


class BatchTransactionAPIView(APIView):
    """
    4. ENDPOINT SOUMISSION PAR LOT (POST)
    Permet de synchroniser plusieurs transactions.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = (JSONParser,)
    serializer_class = BatchTransactionPayloadSerializer

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Soumettre des transactions par lot",
            description="Synchronise plusieurs transactions saisies hors-ligne.",
            request=BatchTransactionPayloadSerializer,
            responses={
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):

        serializer = BatchTransactionPayloadSerializer(
            data=request.data
        )

        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )

        results = []

        for item in serializer.validated_data['transactions']:

            client_ref = item.get(
                'client_ref',
                ''
            )

            try:

                result = (
                    FemiAgentManager
                    .process_transaction_text(
                        text_input=item['text'],
                        source=item.get(
                            'source',
                            'MOBILE'
                        ),
                        entreprise_id=(
                            request.user.entreprise.id
                            if request.user.entreprise
                            else None
                        ),
                        utilisateur_id=request.user.id
                    )
                )

                if not result.success:

                    results.append({
                        "client_ref": client_ref,
                        "status": "error",
                        "message": result.message
                    })

                    continue

                if result.operation_instance is None:

                    results.append({
                        "client_ref": client_ref,
                        "status": "processed_no_save",
                        "message": result.message
                    })

                else:

                    results.append({
                        "client_ref": client_ref,
                        "status": "created",
                        "operation": (
                            OperationModelSerializer(
                                result.operation_instance
                            ).data
                        )
                    })

            except Exception as e:

                results.append({
                    "client_ref": client_ref,
                    "status": "error",
                    "message": str(e)
                })

        return Response(
            {"results": results},
            status=status.HTTP_200_OK
        )


class ExportTransactionAPIView(APIView):
    """
    5. ENDPOINT EXPORT FISCAL (GET)
    Génère un export PDF ou Excel.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Exporter les opérations (PDF ou Excel)",
            description="Génère un export financier.",
            parameters=[
                OpenApiParameter(
                    name='export_format',
                    type=OpenApiTypes.STR,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    default='xlsx',
                    description="Format du fichier exporté",
                    enum=['pdf', 'xlsx']
                ),
                OpenApiParameter(
                    name='date_from',
                    type=OpenApiTypes.DATE,
                    location=OpenApiParameter.QUERY,
                    required=False
                ),
                OpenApiParameter(
                    name='date_to',
                    type=OpenApiTypes.DATE,
                    location=OpenApiParameter.QUERY,
                    required=False
                ),
            ],
            responses={
                200: OpenApiTypes.BINARY,
                400: OpenApiTypes.OBJECT,
                404: OpenApiTypes.OBJECT
            }
        )
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)
    else:
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)

    def _handle_get(self, request, *args, **kwargs):

        export_format = request.query_params.get(
            'export_format',
            'xlsx'
        ).lower()

        if export_format not in ('pdf', 'xlsx'):

            return Response(
                {
                    "error": (
                        "Le paramètre 'format' doit être "
                        "'pdf' ou 'xlsx'."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        entreprise = request.user.entreprise

        if not entreprise:

            return Response(
                {
                    "error": "Aucune entreprise configurée"
                },
                status=status.HTTP_404_NOT_FOUND
            )

        date_from = request.query_params.get(
            'date_from'
        )

        date_to = request.query_params.get(
            'date_to'
        )

        ops = (
            Operation.objects
            .filter(entreprise=entreprise)
            .order_by('transaction_date')
        )

        if date_from:
            ops = ops.filter(
                transaction_date__gte=date_from
            )

        if date_to:
            ops = ops.filter(
                transaction_date__lte=date_to
            )

        if export_format == 'xlsx':
            return self._export_excel(
                ops,
                entreprise
            )

        return self._export_pdf(
            ops,
            entreprise
        )

    def _export_excel(self, ops, entreprise):

        from openpyxl import Workbook
        from django.http import HttpResponse as DjangoHttpResponse

        wb = Workbook()

        ws = wb.active
        ws.title = "Opérations"

        ws.append([
            "Date",
            "Type",
            "Catégorie",
            "Montant HT",
            "TVA",
            "Montant TTC",
            "Devise",
            "Fournisseur/Client",
            "Mode de paiement",
            "Description"
        ])

        for op in ops:

            ws.append([
                str(op.transaction_date),
                op.transaction_type,
                op.category or "",
                (
                    float(op.amount_ht)
                    if op.amount_ht is not None
                    else ""
                ),
                (
                    float(op.tax_amount)
                    if op.tax_amount is not None
                    else ""
                ),
                (
                    float(op.amount_ttc)
                    if op.amount_ttc is not None
                    else ""
                ),
                op.currency or entreprise.devise,
                op.vendor_or_client or "",
                op.payment_method or "",
                op.description or "",
            ])

        response = DjangoHttpResponse(
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            )
        )

        response['Content-Disposition'] = (
            f'attachment; filename="operations_'
            f'{entreprise.nom}.xlsx"'
        )

        wb.save(response)

        return response

    def _export_pdf(self, ops, entreprise):

        import io

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import (
            landscape,
            A4
        )
        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph
        )
        from reportlab.lib.styles import (
            getSampleStyleSheet
        )
        from django.http import HttpResponse as DjangoHttpResponse

        buffer = io.BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4)
        )

        styles = getSampleStyleSheet()

        elements = [
            Paragraph(
                f"Export des opérations — {entreprise.nom}",
                styles['Title']
            )
        ]

        data = [[
            "Date",
            "Type",
            "Catégorie",
            "Montant TTC",
            "Devise",
            "Fournisseur/Client",
            "Paiement"
        ]]

        for op in ops:

            data.append([
                str(op.transaction_date),
                op.transaction_type,
                op.category or "",
                (
                    str(op.amount_ttc)
                    if op.amount_ttc is not None
                    else ""
                ),
                op.currency or entreprise.devise,
                op.vendor_or_client or "",
                op.payment_method or "",
            ])

        table = Table(
            data,
            repeatRows=1
        )

        table.setStyle(
            TableStyle([
                (
                    'BACKGROUND',
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#2c3e50")
                ),
                (
                    'TEXTCOLOR',
                    (0, 0),
                    (-1, 0),
                    colors.white
                ),
                (
                    'GRID',
                    (0, 0),
                    (-1, -1),
                    0.5,
                    colors.grey
                ),
                (
                    'FONTSIZE',
                    (0, 0),
                    (-1, -1),
                    8
                ),
            ])
        )

        elements.append(table)

        doc.build(elements)

        buffer.seek(0)

        response = DjangoHttpResponse(
            buffer,
            content_type='application/pdf'
        )

        response['Content-Disposition'] = (
            f'attachment; filename="operations_'
            f'{entreprise.nom}.pdf"'
        )

        return response


class LoginAPIView(APIView):
    """
    Authentification par username/password.
    Renvoie un token DRF.
    """

    authentication_classes = []
    permission_classes = []

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Se connecter et obtenir un token",
            description=(
                "Authentifie un utilisateur et renvoie "
                "un token DRF."
            ),
            request={
                "application/json": {
                    "type": "object",
                    "properties": {
                        "username": {
                            "type": "string",
                            "example": "test_api"
                        },
                        "password": {
                            "type": "string",
                            "example": "test1234"
                        },
                    },
                    "required": [
                        "username",
                        "password"
                    ],
                }
            },
            responses={
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT,
                401: OpenApiTypes.OBJECT
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):

        identifiant = request.data.get('identifiant') or request.data.get('username')
        password = request.data.get('password')

        if not identifiant or not password:
            return Response(
                {"error": "identifiant et password sont requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        resolved_username = identifiant
        if '@' in identifiant:
            utilisateur = Utilisateur.objects.filter(email__iexact=identifiant).first()
            if utilisateur:
                resolved_username = utilisateur.username

        user = authenticate(username=resolved_username, password=password)

        if not user:
            return Response(
                {"error": "Identifiants invalides."},
                status=status.HTTP_401_UNAUTHORIZED
            )

        token, _ = Token.objects.get_or_create(user=user)

        return Response({
            "token": token.key,
            "utilisateur_id": str(user.id),
            "role": user.role,
            "entreprise_id": (
                str(user.entreprise.id) if user.entreprise else None
            ),
            "entreprise_nom": (
                user.entreprise.nom if user.entreprise else None
            ),
        })

class LogoutAPIView(APIView):
    """
    Déconnexion : supprime le token DRF de l'utilisateur authentifié.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Se déconnecter",
            description="Supprime le token DRF courant de l'utilisateur authentifié.",
            responses={
                200: OpenApiTypes.OBJECT,
                401: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        Token.objects.filter(user=request.user).delete()
        return Response(
            {"message": "Déconnexion réussie."},
            status=status.HTTP_200_OK,
        )


class KpiNiveauAPIView(APIView):
    """
    6. ENDPOINT KPI PAR NIVEAU (GET)
    Renvoie les KPI du catalogue (modèle Kpi) pour un niveau donné,
    filtrés selon le secteur de l'entreprise, avec leur valeur calculée
    quand une fonction de calcul existe pour ce KPI.
    """

    permission_classes = [IsAuthenticated]

    # Registre des calculateurs, indexé par le nom exact du Kpi en base.
    # Chaque fonction reçoit (entreprise, ops_periode, toutes_ops) et
    # renvoie la valeur calculée.
    def _calculateurs(self):
        return {
            "Chiffre d'affaires": self._calc_chiffre_affaires,
            "Bénéfice": self._calc_benefice,
            "Marge": self._calc_marge,
            "Dépenses": self._calc_depenses,
            "Trésorerie": self._calc_tresorerie,
            "Clients": self._calc_clients,
            "Créances": self._calc_creances,
            "Prêts accordés": self._calc_prets_accordes,
            "Dettes": self._calc_dettes,
            "Ventes": self._calc_nombre_ventes,
            "Panier moyen": self._calc_panier_moyen,
            "Nombre de prestations": self._calc_nombre_prestations,
            "Heures facturées": self._calc_heures_facturees,
            "Marge par prestation": self._calc_marge_par_prestation,
        }

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="KPI d'un niveau donné (catalogue Kpi)",
            description="Renvoie les KPI du niveau demandé, filtrés par secteur, avec valeur calculée si disponible.",
            parameters=[
                OpenApiParameter(
                    name='period', type=OpenApiTypes.STR, location=OpenApiParameter.QUERY,
                    required=False, default='this_month',
                    enum=['today', 'this_month', 'last_month', 'this_year'],
                ),
            ],
            responses={200: OpenApiTypes.OBJECT}
        )
        def get(self, request, numero, *args, **kwargs):
            return self._handle_get(request, numero)
    else:
        def get(self, request, numero, *args, **kwargs):
            return self._handle_get(request, numero)

    def _handle_get(self, request, numero):
        entreprise = request.user.entreprise
        if not entreprise:
            return Response({"error": "Aucune entreprise configurée"}, status=status.HTTP_404_NOT_FOUND)

        try:
            niveau = Niveau.objects.get(numero=numero)
        except Niveau.DoesNotExist:
            return Response({"error": f"Niveau {numero} introuvable."}, status=status.HTTP_404_NOT_FOUND)

                # Priorité secteur : si des KPI spécifiques existent pour ce secteur, ils
        # remplacent ENTIÈREMENT les génériques pour ce niveau (pas d'addition).
        kpis_specifiques = Kpi.objects.filter(niveau=niveau, secteur=entreprise.secteur)
        if entreprise.secteur and kpis_specifiques.exists():
            kpis_catalogue = kpis_specifiques
        else:
            kpis_catalogue = Kpi.objects.filter(niveau=niveau, secteur__isnull=True)

        period = request.query_params.get('period', 'this_month')
        now = timezone.now()
        ops_periode = self._filtrer_periode(Operation.objects.filter(entreprise=entreprise), period, now)
        toutes_ops = Operation.objects.filter(entreprise=entreprise)

        calculateurs = self._calculateurs()
        resultats = []

        for kpi in kpis_catalogue:
            calc = calculateurs.get(kpi.nom)
            valeur = calc(entreprise, ops_periode, toutes_ops) if calc else None

            resultats.append({
                "nom": kpi.nom,
                "icone": kpi.icone,
                "unite": kpi.unite,
                "description": kpi.formule_description,
                "disponible": calc is not None,
                "valeur": valeur,
            })

        return Response({
            "niveau": {"numero": niveau.numero, "nom": niveau.nom},
            "period": period,
            "kpis": resultats,
        })

    def _filtrer_periode(self, ops, period, now):
        if period == 'today':
            return ops.filter(transaction_date=now.date())
        if period == 'this_month':
            return ops.filter(transaction_date__year=now.year, transaction_date__month=now.month)
        if period == 'last_month':
            month = 12 if now.month == 1 else now.month - 1
            year = now.year - 1 if now.month == 1 else now.year
            return ops.filter(transaction_date__year=year, transaction_date__month=month)
        if period == 'this_year':
            return ops.filter(transaction_date__year=now.year)
        return ops

    # --- Calculateurs Niveau 1 ---

    def _calc_chiffre_affaires(self, entreprise, ops_periode, toutes_ops):
        total = ops_periode.filter(transaction_type="RECETTE").aggregate(t=Sum('amount_ttc'))['t']
        return float(total or Decimal('0.00'))

    def _calc_depenses(self, entreprise, ops_periode, toutes_ops):
        total = ops_periode.filter(transaction_type="DEPENSE").aggregate(t=Sum('amount_ttc'))['t']
        return float(total or Decimal('0.00'))

    def _calc_benefice(self, entreprise, ops_periode, toutes_ops):
        return self._calc_chiffre_affaires(entreprise, ops_periode, toutes_ops) - self._calc_depenses(entreprise, ops_periode, toutes_ops)

    def _calc_tresorerie(self, entreprise, ops_periode, toutes_ops):
        encaisse = toutes_ops.filter(transaction_type="RECETTE").aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        decaisse = toutes_ops.filter(transaction_type="DEPENSE").aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')

        prets_donnes = toutes_ops.filter(transaction_type="PRET_DONNE")
        sorti_prets = prets_donnes.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        rembourse_recu = prets_donnes.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')

        prets_recus = toutes_ops.filter(transaction_type="PRET_RECU")
        entre_prets = prets_recus.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        rembourse_verse = prets_recus.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')

        return float(
            encaisse - decaisse
            - sorti_prets + rembourse_recu
            + entre_prets - rembourse_verse
        )

    def _calc_clients(self, entreprise, ops_periode, toutes_ops):
        return Contact.objects.filter(
            entreprise=entreprise, type="CLIENT", operations__in=ops_periode
        ).distinct().count()

    def _calc_creances(self, entreprise, ops_periode, toutes_ops):
        creances_ops = toutes_ops.filter(transaction_type="RECETTE", statut_paiement="CREDIT")
        du = creances_ops.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        paye = creances_ops.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        return float(du - paye)
    
    def _calc_marge(self, entreprise, ops_periode, toutes_ops):
        ca = self._calc_chiffre_affaires(entreprise, ops_periode, toutes_ops)
        if ca <= 0:
            return 0.0
        depenses = self._calc_depenses(entreprise, ops_periode, toutes_ops)
        return round(((ca - depenses) / ca) * 100, 2)

    def _calc_panier_moyen(self, entreprise, ops_periode, toutes_ops):
        ventes = ops_periode.filter(transaction_type="RECETTE")
        nb_ventes = ventes.count()
        if nb_ventes == 0:
            return 0.0
        total = ventes.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        return round(float(total) / nb_ventes, 2)

    def _calc_nombre_ventes(self, entreprise, ops_periode, toutes_ops):
        return ops_periode.filter(transaction_type="RECETTE").count()
    
    def _calc_prets_accordes(self, entreprise, ops_periode, toutes_ops):
        qs = toutes_ops.filter(transaction_type="PRET_DONNE", statut_paiement="CREDIT")
        prete = qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        rembourse = qs.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        return float(prete - rembourse)

    def _calc_dettes(self, entreprise, ops_periode, toutes_ops):
        qs = toutes_ops.filter(transaction_type="PRET_RECU", statut_paiement="CREDIT")
        emprunte = qs.aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        rembourse = qs.aggregate(t=Sum('montant_paye'))['t'] or Decimal('0.00')
        return float(emprunte - rembourse)
    
    
    def _calc_nombre_prestations(self, entreprise, ops_periode, toutes_ops):
        return PrestationRealisee.objects.filter(
            operation__in=ops_periode
        ).aggregate(t=Sum('quantite'))['t'] or 0

    def _calc_heures_facturees(self, entreprise, ops_periode, toutes_ops):
        total_minutes = PrestationRealisee.objects.filter(
            operation__in=ops_periode
        ).aggregate(t=Sum('duree_minutes'))['t'] or 0
        return round(total_minutes / 60, 2)

    def _calc_marge_par_prestation(self, entreprise, ops_periode, toutes_ops):
        lignes = PrestationRealisee.objects.filter(
            operation__in=ops_periode, prestation__cout_unitaire__isnull=False
        ).select_related('prestation')
        if not lignes.exists():
            return None
        return [
            {
                "prestation": l.prestation.nom,
                "marge_unitaire": float(l.prix_unitaire_facture - l.prestation.cout_unitaire),
            }
            for l in lignes
        ]
        
        
class DeactivateAccountAPIView(APIView):
    """
    Désactivation du compte de l'utilisateur authentifié (soft delete).
    Les données restent conservées ; le compte ne peut plus se reconnecter
    tant qu'il n'est pas réactivé manuellement (ex. par un admin).
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Désactiver son compte",
            description=(
                "Désactive le compte de l'utilisateur authentifié (is_active=False) "
                "et supprime son token. Les données restent conservées."
            ),
            responses={
                200: OpenApiTypes.OBJECT,
                401: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        user = request.user
        user.is_active = False
        user.save(update_fields=['is_active'])

        Token.objects.filter(user=user).delete()

        return Response(
            {"message": "Compte désactivé avec succès."},
            status=status.HTTP_200_OK,
        )       


class LinkWhatsAppRequestAPIView(APIView):
    """
    Étape 1 : l'utilisateur authentifié demande à lier un numéro WhatsApp
    à son compte. Un code OTP à 6 chiffres est envoyé sur ce numéro via
    WhatsApp ; il doit être confirmé via LinkWhatsAppConfirmAPIView.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Demander la liaison d'un numéro WhatsApp",
            description=(
                "Envoie un code OTP par WhatsApp au numéro fourni, pour vérifier "
                "que l'utilisateur en est bien le propriétaire avant liaison."
            ),
            request={
                "application/json": {
                    "type": "object",
                    "properties": {
                        "telephone_whatsapp": {"type": "string", "example": "+22900000001"},
                    },
                    "required": ["telephone_whatsapp"],
                }
            },
            responses={
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT,
                409: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        telephone = request.data.get('telephone_whatsapp')

        if not telephone:
            return Response(
                {"error": "telephone_whatsapp est requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        telephone_normalise = _normalize_phone(telephone)

        # Empêche de lier un numéro déjà utilisé par un AUTRE utilisateur.
        deja_pris = Utilisateur.objects.filter(
            telephone_whatsapp=telephone_normalise
        ).exclude(id=request.user.id).exists()

        if deja_pris:
            return Response(
                {"error": "Ce numéro WhatsApp est déjà associé à un autre compte."},
                status=status.HTTP_409_CONFLICT
            )

        code = WhatsAppLinkRequest.generer_code()

        demande = WhatsAppLinkRequest.objects.create(
            utilisateur=request.user,
            telephone_whatsapp=telephone_normalise,
            code=code,
        )

        client = WhatsAppClient()
        envoye = client.send_text_message(
            telephone_normalise,
            f"Femi : votre code de vérification est {code}. Il expire dans 10 minutes."
        )

        if not envoye:
            demande.delete()
            return Response(
                {"error": "Échec de l'envoi du code WhatsApp. Vérifiez le numéro et réessayez."},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {"message": "Code envoyé par WhatsApp.", "demande_id": str(demande.id)},
            status=status.HTTP_200_OK
        )


class LinkWhatsAppConfirmAPIView(APIView):
    """
    Étape 2 : l'utilisateur confirme le code OTP reçu par WhatsApp,
    ce qui finalise la liaison du numéro à son compte.
    """

    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Confirmer la liaison WhatsApp avec le code OTP",
            request={
                "application/json": {
                    "type": "object",
                    "properties": {
                        "demande_id": {"type": "string"},
                        "code": {"type": "string", "example": "123456"},
                    },
                    "required": ["demande_id", "code"],
                }
            },
            responses={
                200: OpenApiTypes.OBJECT,
                400: OpenApiTypes.OBJECT,
                404: OpenApiTypes.OBJECT,
            }
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        demande_id = request.data.get('demande_id')
        code = request.data.get('code')

        if not demande_id or not code:
            return Response(
                {"error": "demande_id et code sont requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            demande = WhatsAppLinkRequest.objects.get(id=demande_id, utilisateur=request.user)
        except WhatsAppLinkRequest.DoesNotExist:
            return Response(
                {"error": "Demande introuvable."},
                status=status.HTTP_404_NOT_FOUND
            )

        if not demande.est_valide():
            return Response(
                {"error": "Code expiré ou trop de tentatives. Recommencez la demande."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if demande.code != code:
            demande.tentatives += 1
            demande.save(update_fields=['tentatives'])
            return Response(
                {"error": "Code incorrect."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Re-vérifie l'unicité au moment de la confirmation (fenêtre de course
        # possible entre la demande et la confirmation).
        deja_pris = Utilisateur.objects.filter(
            telephone_whatsapp=demande.telephone_whatsapp
        ).exclude(id=request.user.id).exists()

        if deja_pris:
            return Response(
                {"error": "Ce numéro WhatsApp est désormais associé à un autre compte."},
                status=status.HTTP_409_CONFLICT
            )

        request.user.telephone_whatsapp = demande.telephone_whatsapp
        request.user.save(update_fields=['telephone_whatsapp'])

        demande.utilisee = True
        demande.save(update_fields=['utilisee'])

        return Response(
            {"message": "Numéro WhatsApp lié avec succès.", "telephone_whatsapp": request.user.telephone_whatsapp},
            status=status.HTTP_200_OK
        )