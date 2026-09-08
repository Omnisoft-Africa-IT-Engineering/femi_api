from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from apps.femi_account.models import (
    Entreprise,
    Utilisateur,
    Operation,
    Secteur,
    Niveau,
    Kpi,
    InsightIA,
    Categorie,
    Contact,
    Plan,
    Abonnement,
)


@admin.register(Entreprise)
class EntrepriseAdmin(admin.ModelAdmin):
    list_display = ('nom', 'secteur', 'rccm', 'ifu', 'regime_fiscal', 'devise', 'created_at')
    list_filter = ('secteur', 'regime_fiscal', 'devise')
    search_fields = ('nom', 'rccm', 'ifu')


@admin.register(Utilisateur)
class UtilisateurAdmin(UserAdmin):
    list_display = ('username', 'email', 'role', 'entreprise', 'telephone_whatsapp', 'is_staff')
    list_filter = ('is_staff', 'is_superuser', 'is_active')


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ('transaction_type', 'amount_ttc', 'currency', 'category', 'payment_method', 'transaction_date', 'entreprise')
    list_filter = ('transaction_type', 'currency', 'payment_method', 'transaction_date', 'entreprise')
    search_fields = ('description', 'vendor_or_client', 'category')


@admin.register(Secteur)
class SecteurAdmin(admin.ModelAdmin):
    list_display = ('nom', 'description')
    search_fields = ('nom',)


@admin.register(Niveau)
class NiveauAdmin(admin.ModelAdmin):
    list_display = ('numero', 'nom')
    ordering = ('numero',)


@admin.register(Kpi)
class KpiAdmin(admin.ModelAdmin):
    list_display = ('nom', 'niveau', 'secteur', 'unite', 'icone')
    list_filter = ('niveau', 'secteur')
    search_fields = ('nom', 'formule_description')


@admin.register(InsightIA)
class InsightIAAdmin(admin.ModelAdmin):
    list_display = ('entreprise', 'type', 'niveau_gravite', 'date_detection')
    list_filter = ('type', 'niveau_gravite', 'entreprise')
    search_fields = ('message', 'action_recommandee')
    readonly_fields = ('date_detection',)


@admin.register(Categorie)
class CategorieAdmin(admin.ModelAdmin):
    list_display = ('nom', 'type', 'entreprise')
    list_filter = ('type', 'entreprise')
    search_fields = ('nom',)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('nom', 'type', 'telephone', 'entreprise')
    list_filter = ('type', 'entreprise')
    search_fields = ('nom', 'telephone')



@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('nom', 'prix', 'est_a_partir_de', 'devise', 'populaire', 'duree_jours')
    list_filter = ('populaire', 'est_a_partir_de', 'devise')
    search_fields = ('nom', 'description')


@admin.register(Abonnement)
class AbonnementAdmin(admin.ModelAdmin):
    list_display = ('entreprise', 'plan', 'statut', 'prix_paye', 'date_debut', 'date_fin', 'created_at')
    list_filter = ('statut', 'plan', 'entreprise')
    search_fields = ('entreprise__nom', 'plan__nom')
    readonly_fields = ('created_at',)