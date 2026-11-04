"""
GET /api/v1/abonnement/  et  GET /api/v1/auth/profile/

Même réponse pour les deux routes : l'app mobile appelle déjà
/auth/profile/ (is_pro, plan, entreprise_nom) ; /abonnement/ est le nom
« propre » pour l'écran d'abonnement. Ces routes restent accessibles même
quand l'abonnement est expiré (voir middleware_abonnement.py).
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.femi_account.abonnement import abonnement_obligatoire, resume_abonnement
from apps.femi_account.models import Plan


class ProfilAbonnementAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        entreprise = getattr(user, "entreprise", None)
        resume = resume_abonnement(entreprise)
        nom_entreprise = entreprise.nom if entreprise else None

        plans = [
            {
                "nom": plan.nom,
                "description": plan.description,
                "prix": float(plan.prix),
                "devise": plan.devise,
                "duree_jours": plan.duree_jours,
                "populaire": plan.populaire,
                "fonctionnalites": plan.fonctionnalites,
            }
            for plan in Plan.objects.order_by("prix")
        ]

        return Response({
            "username": user.username,
            "role": user.role,
            "entreprise_nom": nom_entreprise,
            "company_name": nom_entreprise,
            "is_pro": resume["actif"],
            "plan": resume["plan"],
            "abonnement": resume,
            "abonnement_obligatoire": abonnement_obligatoire(),
            "plans": plans,
        })
