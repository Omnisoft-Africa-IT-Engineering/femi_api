from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from apps.femi_account.models import Entreprise, Utilisateur, Operation

@admin.register(Entreprise)
class EntrepriseAdmin(admin.ModelAdmin):
    list_display = ('nom', 'rccm', 'ifu', 'regime_fiscal', 'devise', 'created_at','adresse')
    search_fields = ('nom', 'rccm', 'ifu')

@admin.register(Utilisateur)
class UtilisateurAdmin(UserAdmin):
    list_display = ('username', 'email', 'role', 'entreprise', 'telephone_whatsapp', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('Informations Femi', {'fields': ('entreprise', 'role', 'telephone_whatsapp')}),
    )

@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ('entreprise', 'transaction_type', 'amount_ttc', 'currency', 'category', 'payment_method', 'transaction_date', 'source')
    list_filter = ('transaction_type', 'payment_method', 'source', 'entreprise')
    search_fields = ('description', 'vendor_or_client', 'category')