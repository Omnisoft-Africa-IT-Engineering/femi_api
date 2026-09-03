from django.contrib.auth import authenticate
from rest_framework.authtoken.models import Token
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser


from django.utils import timezone
from django.db.models import Sum, Avg
from decimal import Decimal

# Importations DRF-Spectacular pour Swagger OAS 3.0
try:
    from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
    HAS_SPECTACULAR = True
except ImportError:
    HAS_SPECTACULAR = False


from apps.femi_api.serializers import TransactionPayloadSerializer, OperationModelSerializer, BatchTransactionPayloadSerializer
from apps.femi_agent.agent.manager import FemiAgentManager
from apps.femi_account.models import Operation, Entreprise


class ProcessTransactionAPIView(APIView):
    """
    1. ENDPOINT UNIFIÉ (POST)
    Gère la réception des transactions (Texte, Image, Audio) et répond aux questions de l'utilisateur.
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
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data
        text = validated_data.get('text')
        image_file = validated_data.get('image')
        audio_file = validated_data.get('audio')
        source = validated_data.get('source', 'MOBILE')

        image_bytes = image_file.read() if image_file else None
        audio_bytes = audio_file.read() if audio_file else None

        # Appel du cerveau IA central
        result = FemiAgentManager.process_transaction_text(
        text_input=text,
        image_bytes=image_bytes,
        audio_bytes=audio_bytes,
        image_file=image_file,
        source=source,
        entreprise_id=request.user.entreprise.id if request.user.entreprise else None,
        utilisateur_id=request.user.id
        )

        if not result.success:
            return Response(
                {"error": result.message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Cas 1 : Question analytique ou bilan (pas d'enregistrement BDD)
        if result.operation_instance is None:
            return Response(
                {"message": result.message},
                status=status.HTTP_200_OK
            )

        # Cas 2 : Enregistrement réussi d'une transaction
        response_data = OperationModelSerializer(result.operation_instance).data
        response_data["message"] = result.message

        return Response(response_data, status=status.HTTP_201_CREATED)


class DashboardKPIAPIView(APIView):
    """
    2. ENDPOINT KPIS DASHBOARD (GET)
    Fournit l'ensemble des indicateurs de performance clés pour le tableau de bord mobile.
    """
    permission_classes = [IsAuthenticated]
    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Récupérer les KPIs du Dashboard",
            description="Renvoie les métriques financières agrégées (Chiffre d'affaires, dépenses, bénéfice net, marge, top catégories) filtrées par période.",
            parameters=[
                OpenApiParameter(
                    name='period',
                    type=OpenApiTypes.STR,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    default='this_month',
                    description="Période d'analyse des métriques",
                    enum=['today', 'this_month', 'last_month', 'this_year']
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
        period = request.query_params.get('period', 'this_month')
        entreprise = request.user.entreprise

        if not entreprise:
            return Response({"error": "Aucune entreprise configurée"}, status=status.HTTP_404_NOT_FOUND)

        now = timezone.now()
        ops = Operation.objects.filter(entreprise=entreprise)

        # Filtre par période
        if period == 'today':
            ops = ops.filter(transaction_date=now.date())
        elif period == 'this_month':
            ops = ops.filter(transaction_date__year=now.year, transaction_date__month=now.month)
        elif period == 'last_month':
            month = 12 if now.month == 1 else now.month - 1
            year = now.year - 1 if now.month == 1 else now.year
            ops = ops.filter(transaction_date__year=year, transaction_date__month=month)
        elif period == 'this_year':
            ops = ops.filter(transaction_date__year=now.year)

        # Calcul des agrégats financiers
        revenue = ops.filter(transaction_type="RECETTE").aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        expenses = ops.filter(transaction_type="DEPENSE").aggregate(t=Sum('amount_ttc'))['t'] or Decimal('0.00')
        avg_sale = ops.filter(transaction_type="RECETTE").aggregate(a=Avg('amount_ttc'))['a'] or Decimal('0.00')
        profit = revenue - expenses
        margin = (profit / revenue * 100) if revenue > 0 else Decimal('0.00')

        # Top 3 des catégories de dépenses
        top_categories = ops.filter(transaction_type="DEPENSE") \
                            .values('category') \
                            .annotate(amount=Sum('amount_ttc')) \
                            .order_by('-amount')[:3]

        # 5 dernières transactions récentes
        recent_ops = ops.order_by('-created_at')[:5].values(
            'id', 'transaction_type', 'amount_ttc', 'category', 'transaction_date'
        )

        return Response({
            "period": period,
            "currency": entreprise.devise or "XOF",
            "kpis": {
                "total_revenue": float(revenue),
                "total_expenses": float(expenses),
                "net_profit": float(profit),
                "profit_margin_percentage": round(float(margin), 2),
                "average_sale_amount": round(float(avg_sale), 2),
                "total_transactions_count": ops.count()
            },
            "top_expense_categories": list(top_categories),
            "recent_transactions": list(recent_ops)
        }, status=status.HTTP_200_OK)
        


class HealthCheckAPIView(APIView):
    """
    3. ENDPOINT HEALTHCHECK (GET)
    Vérifie que l'API et la connexion à la base de données Supabase répondent.
    """
    authentication_classes = []
    permission_classes = []

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Vérifier l'état de l'API",
            description="Endpoint public de healthcheck. Vérifie que l'API répond et que la connexion à la base de données Supabase fonctionne.",
            responses={200: OpenApiTypes.OBJECT}
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
                "status": "ok" if overall_ok else "degraded",
                "database": db_status,
                "timestamp": timezone.now().isoformat()
            },
            status=status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        )


class BatchTransactionAPIView(APIView):
    """
    4. ENDPOINT SOUMISSION PAR LOT (POST)
    Permet à l'app mobile de synchroniser plusieurs transactions saisies hors-ligne.
    Texte uniquement pour l'instant (voir note serializers.py).
    """
    permission_classes = [IsAuthenticated]
    parser_classes = (JSONParser,)
    serializer_class = BatchTransactionPayloadSerializer

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Soumettre des transactions par lot",
            description="Permet à l'app mobile de synchroniser plusieurs transactions saisies hors-ligne (texte uniquement, 50 maximum par requête). Chaque item peut inclure un 'client_ref' pour réconcilier les données côté app mobile.",
            request=BatchTransactionPayloadSerializer,
            responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT}
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        serializer = BatchTransactionPayloadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        results = []
        for item in serializer.validated_data['transactions']:
            client_ref = item.get('client_ref', '')
            try:
                result = FemiAgentManager.process_transaction_text(
                text_input=item['text'],
                source=item.get('source', 'MOBILE'),
                entreprise_id=request.user.entreprise.id if request.user.entreprise else None,
                utilisateur_id=request.user.id
                )
                if not result.success:
                    results.append({"client_ref": client_ref, "status": "error", "message": result.message})
                    continue

                if result.operation_instance is None:
                    results.append({"client_ref": client_ref, "status": "processed_no_save", "message": result.message})
                else:
                    results.append({
                        "client_ref": client_ref,
                        "status": "created",
                        "operation": OperationModelSerializer(result.operation_instance).data
                    })
            except Exception as e:
                results.append({"client_ref": client_ref, "status": "error", "message": str(e)})

        return Response({"results": results}, status=status.HTTP_200_OK)


class ExportTransactionAPIView(APIView):
    """
    5. ENDPOINT EXPORT FISCAL (GET)
    Génère un export PDF ou Excel des opérations sur une période donnée.
    Query params : format=pdf|xlsx (défaut xlsx), date_from=YYYY-MM-DD, date_to=YYYY-MM-DD
    """
    permission_classes = [IsAuthenticated]

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Exporter les opérations (PDF ou Excel)",
            description="Génère un export des opérations financières sur une période donnée, au format PDF ou Excel (xlsx).",
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
                    required=False,
                    description="Date de début (YYYY-MM-DD)"
                ),
                OpenApiParameter(
                    name='date_to',
                    type=OpenApiTypes.DATE,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    description="Date de fin (YYYY-MM-DD)"
                ),
            ],
            responses={200: OpenApiTypes.BINARY, 400: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT}
        )
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)
    else:
        def get(self, request, *args, **kwargs):
            return self._handle_get(request, *args, **kwargs)

    def _handle_get(self, request, *args, **kwargs):
        export_format = request.query_params.get('export_format', 'xlsx').lower()
        if export_format not in ('pdf', 'xlsx'):
            return Response({"error": "Le paramètre 'format' doit être 'pdf' ou 'xlsx'."}, status=status.HTTP_400_BAD_REQUEST)

        entreprise = request.user.entreprise
        if not entreprise:
            return Response({"error": "Aucune entreprise configurée"}, status=status.HTTP_404_NOT_FOUND)

        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')

        ops = Operation.objects.filter(entreprise=entreprise).order_by('transaction_date')
        if date_from:
            ops = ops.filter(transaction_date__gte=date_from)
        if date_to:
            ops = ops.filter(transaction_date__lte=date_to)

        if export_format == 'xlsx':
            return self._export_excel(ops, entreprise)
        return self._export_pdf(ops, entreprise)

    def _export_excel(self, ops, entreprise):
        from openpyxl import Workbook
        from django.http import HttpResponse as DjangoHttpResponse

        wb = Workbook()
        ws = wb.active
        ws.title = "Opérations"
        ws.append(["Date", "Type", "Catégorie", "Montant HT", "TVA", "Montant TTC", "Devise", "Fournisseur/Client", "Mode de paiement", "Description"])

        for op in ops:
            ws.append([
                str(op.transaction_date),
                op.transaction_type,
                op.category or "",
                float(op.amount_ht) if op.amount_ht is not None else "",
                float(op.tax_amount) if op.tax_amount is not None else "",
                float(op.amount_ttc) if op.amount_ttc is not None else "",
                op.currency or entreprise.devise,
                op.vendor_or_client or "",
                op.payment_method or "",
                op.description or "",
            ])

        response = DjangoHttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response['Content-Disposition'] = f'attachment; filename="operations_{entreprise.nom}.xlsx"'
        wb.save(response)
        return response

    def _export_pdf(self, ops, entreprise):
        import io
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
        from reportlab.lib.styles import getSampleStyleSheet
        from django.http import HttpResponse as DjangoHttpResponse

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
        styles = getSampleStyleSheet()
        elements = [Paragraph(f"Export des opérations — {entreprise.nom}", styles['Title'])]

        data = [["Date", "Type", "Catégorie", "Montant TTC", "Devise", "Fournisseur/Client", "Paiement"]]
        for op in ops:
            data.append([
                str(op.transaction_date),
                op.transaction_type,
                op.category or "",
                str(op.amount_ttc) if op.amount_ttc is not None else "",
                op.currency or entreprise.devise,
                op.vendor_or_client or "",
                op.payment_method or "",
            ])

        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
        ]))
        elements.append(table)
        doc.build(elements)

        buffer.seek(0)
        response = DjangoHttpResponse(buffer, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="operations_{entreprise.nom}.pdf"'
        return response

class LoginAPIView(APIView):
    """
    Authentification par username/password. Renvoie un token DRF.
    Aucune auth requise pour accéder à cette vue (c'est elle qui la produit).
    """
    authentication_classes = []
    permission_classes = []

    if HAS_SPECTACULAR:
        @extend_schema(
            summary="Se connecter et obtenir un token",
            description="Authentifie un utilisateur (username + password) et renvoie un token à utiliser dans le header 'Authorization: Token <valeur>' pour tous les autres endpoints (sauf /health/).",
            request={
                "application/json": {
                    "type": "object",
                    "properties": {
                        "username": {"type": "string", "example": "test_api"},
                        "password": {"type": "string", "example": "test1234"},
                    },
                    "required": ["username", "password"],
                }
            },
            responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT}
        )
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)
    else:
        def post(self, request, *args, **kwargs):
            return self._handle_post(request, *args, **kwargs)

    def _handle_post(self, request, *args, **kwargs):
        username = request.data.get('username')
        password = request.data.get('password')

        if not username or not password:
            return Response(
                {"error": "username et password sont requis."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = authenticate(username=username, password=password)
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
            "entreprise_id": str(user.entreprise.id) if user.entreprise else None,
            "entreprise_nom": user.entreprise.nom if user.entreprise else None,
        })