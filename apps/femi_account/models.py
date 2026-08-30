import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser


class Entreprise(models.Model):
    """Représente une PME / Entreprise cliente de Femi."""
    
    REGIME_CHOICES = [
        ('TPS', 'Taxe Professionnelle Synthétique'),
        ('REEL_SIMPLIFIE', 'Régime Réel Simplifié'),
        ('REEL_NORMAL', 'Régime Réel Normal'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nom = models.CharField(max_length=255, verbose_name="Nom de l'entreprise")
    rccm = models.CharField(max_length=100, blank=True, null=True, verbose_name="N° RCCM")
    ifu = models.CharField(max_length=100, blank=True, null=True, verbose_name="N° IFU / NIF")
    regime_fiscal = models.CharField(max_length=50, choices=REGIME_CHOICES, default='TPS')
    adresse = models.TextField(blank=True, null=True)
    telephone = models.CharField(max_length=30, blank=True, null=True)
    devise = models.CharField(max_length=5, default="XOF")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Entreprise"
        verbose_name_plural = "Entreprises"

    def __str__(self):
        return self.nom


class Utilisateur(AbstractUser):
    """Utilisateur du système Femi (Administrateur, Comptable, Dirigeant, Employé)."""
    
    ROLE_CHOICES = [
        ('ADMIN', 'Administrateur Système'),
        ('DIRIGEANT', 'Dirigeant / Promoteur'),
        ('COMPTABLE', 'Comptable / Expert-Comptable'),
        ('EMPLOYE', 'Employé / Saisie'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entreprise = models.ForeignKey(
        Entreprise, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="utilisateurs"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='DIRIGEANT')
    telephone_whatsapp = models.CharField(
        max_length=30, 
        blank=True, 
        null=True, 
        unique=True,
        help_text="Numéro au format international (ex: +22890000000) pour l'identification WhatsApp"
    )

    class Meta:
        verbose_name = "Utilisateur"
        verbose_name_plural = "Utilisateurs"

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


class Operation(models.Model):
    """Représente une opération comptable (Recette ou Dépense) enregistrée."""

    TYPE_CHOICES = [
        ('RECETTE', 'Recette (Vente / Encaissement)'),
        ('DEPENSE', 'Dépense (Achat / Décaissement)'),
    ]

    SOURCE_CHOICES = [
        ('WHATSAPP', 'WhatsApp'),
        ('MOBILE', 'Application Mobile'),
        ('API', 'API Externe'),
    ]

    PAYMENT_CHOICES = [
        ('CASH', 'Espèces'),
        ('BANK_TRANSFER', 'Virement Bancaire'),
        ('MOBILE_MONEY', 'Mobile Money'),
        ('CARD', 'Carte Bancaire'),
        ('UNKNOWN', 'Inconnu'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Relations
    entreprise = models.ForeignKey(
        Entreprise, 
        on_delete=models.CASCADE, 
        related_name="operations"
    )
    cree_par = models.ForeignKey(
        Utilisateur, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="operations_creees"
    )

    # Données Financières
    transaction_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    amount_ht = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    amount_ttc = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=5, default="XOF")
    
    # Qualification Comptable
    category = models.CharField(max_length=100)
    vendor_or_client = models.CharField(max_length=255, null=True, blank=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='UNKNOWN')
    transaction_date = models.DateField(null=True, blank=True)
    description = models.TextField()

    # Métadonnées IA & Traçabilité
    confidence_score = models.FloatField(default=1.0)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='API')
    raw_input_text = models.TextField(null=True, blank=True)
    receipt_image = models.ImageField(upload_to='receipts/', null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Opération Comptable"
        verbose_name_plural = "Opérations Comptables"

    def __str__(self):
        return f"[{self.entreprise.nom}] {self.transaction_type} - {self.amount_ttc} {self.currency}"