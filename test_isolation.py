from django.db import transaction
from django.urls import reverse
from rest_framework.test import APIClient

from apps.femi_account.models import Operation, Utilisateur


class Rollback(Exception):
    pass


admin = Utilisateur.objects.filter(role__iexact="ADMIN", entreprise__isnull=False).first()
avant = Operation.objects.filter(entreprise=admin.entreprise).count()

try:
    with transaction.atomic():
        sans = Utilisateur(username="zz_sans_entreprise", role="EMPLOYE")
        sans.set_unusable_password()
        sans.save()

        c = APIClient()
        c.force_authenticate(user=sans)
        r = c.post(reverse("api_process_transaction"),
                   {"text": "vente de 1000 francs", "entreprise_id": str(admin.entreprise_id)},
                   format="json")
        print("Compte sans entreprise :", r.status_code, "(attendu 403)", r.json())

        apres = Operation.objects.filter(entreprise=admin.entreprise).count()
        print("Aucune operation creee :", apres == avant, "(attendu True)")

        raise Rollback()
except Rollback:
    print("Rollback fait.")

print("Restes en base :", Utilisateur.objects.filter(username="zz_sans_entreprise").count(), "(attendu 0)")
