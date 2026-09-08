import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser


class Utilisateur(AbstractUser):
    """Modèle d'utilisateur personnalisé pour Femi."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entreprise = models.ForeignKey(
        'Entreprise',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="utilisateurs"
    )
    role = models.CharField(max_length=50, blank=True, null=True)
    telephone_whatsapp = models.CharField(max_length=30, blank=True, null=True)

    class Meta:
        verbose_name = "Utilisateur"
        verbose_name_plural = "Utilisateurs"

    def __str__(self):
        return self.username or self.email


class Secteur(models.Model):
    """Secteur d'activité d'une entreprise (Commerce, Restauration, etc.)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nom = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Secteur d'activité"
        verbose_name_plural = "Secteurs d'activité"

    def __str__(self):
        return self.nom


class Entreprise(models.Model):
    """Modèle représentant une entreprise cliente."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nom = models.CharField(max_length=255)
    secteur = models.ForeignKey(
        Secteur,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entreprises"
    )
    rccm = models.CharField(max_length=100, blank=True, null=True)
    ifu = models.CharField(max_length=100, blank=True, null=True)
    regime_fiscal = models.CharField(max_length=100, blank=True, null=True)
    devise = models.CharField(max_length=10, default="XOF")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Entreprise"
        verbose_name_plural = "Entreprises"

    def __str__(self):
        return self.nom


class Operation(models.Model):
    """Opération financière ou transaction enregistrée."""
    TRANSACTION_TYPES = [
        ('RECETTE', 'Recette'),
        ('DEPENSE', 'Dépense'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entreprise = models.ForeignKey(
        Entreprise,
        on_delete=models.CASCADE,
        related_name="operations"
    )
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    amount_ttc = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="XOF")
    category = models.CharField(max_length=100, blank=True, null=True)
    payment_method = models.CharField(max_length=50, blank=True, null=True)
    transaction_date = models.DateField()
    description = models.TextField(blank=True, null=True)
    vendor_or_client = models.CharField(max_length=255, blank=True, null=True)
    source = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        verbose_name = "Opération"
        verbose_name_plural = "Opérations"

    def __str__(self):
        return f"{self.transaction_type} - {self.amount_ttc} {self.currency} ({self.entreprise.nom})"


class Niveau(models.Model):
    """Les 5 niveaux de lecture du dashboard Femi."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    numero = models.PositiveSmallIntegerField(unique=True)
    nom = models.CharField(max_length=100)

    class Meta:
        verbose_name = "Niveau"
        verbose_name_plural = "Niveaux"
        ordering = ['numero']

    def __str__(self):
        return f"Niveau {self.numero} — {self.nom}"


class Kpi(models.Model):
    """Indicateur clé de performance, rattaché à un niveau et un secteur."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    niveau = models.ForeignKey(
        Niveau,
        on_delete=models.CASCADE,
        related_name="kpis"
    )
    secteur = models.ForeignKey(
        Secteur,
        on_delete=models.CASCADE,
        related_name="kpis",
        null=True,
        blank=True,
        help_text="Laisser vide si le KPI est commun à tous les secteurs"
    )
    nom = models.CharField(max_length=150)
    icone = models.CharField(max_length=10, blank=True, null=True)
    formule_description = models.TextField(blank=True, null=True)
    unite = models.CharField(max_length=20, blank=True, null=True)

    class Meta:
        verbose_name = "KPI"
        verbose_name_plural = "KPIs"

    def __str__(self):
        return f"{self.nom} ({self.niveau})"


class InsightIA(models.Model):
    """Résultat d'analyse généré par l'IA pour une entreprise (Niveau 5)."""
    TYPE_CHOICES = [
        ('ANOMALIE', 'Anomalie'),
        ('TENDANCE', 'Tendance'),
        ('PREVISION', 'Prévision'),
        ('ALERTE', 'Alerte'),
        ('RECOMMANDATION', 'Recommandation'),
        ('OPPORTUNITE', 'Opportunité'),
    ]
    GRAVITE_CHOICES = [
        ('INFO', 'Information'),
        ('ATTENTION', 'Attention'),
        ('CRITIQUE', 'Critique'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entreprise = models.ForeignKey(
        Entreprise,
        on_delete=models.CASCADE,
        related_name="insights"
    )
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    message = models.TextField()
    action_recommandee = models.TextField(blank=True, null=True)
    niveau_gravite = models.CharField(max_length=20, choices=GRAVITE_CHOICES, default='INFO')
    date_detection = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Insight IA"
        verbose_name_plural = "Insights IA"
        ordering = ['-date_detection']

    def __str__(self):
        return f"[{self.type}] {self.entreprise.nom} - {self.message[:50]}"


class Categorie(models.Model):
    """Catégorie de transaction, propre à une entreprise."""
    TYPE_CHOICES = [
        ('RECETTE', 'Recette'),
        ('DEPENSE', 'Dépense'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entreprise = models.ForeignKey(
        Entreprise,
        on_delete=models.CASCADE,
        related_name="categories"
    )
    nom = models.CharField(max_length=100)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)

    class Meta:
        verbose_name = "Catégorie"
        verbose_name_plural = "Catégories"
        unique_together = ('entreprise', 'nom', 'type')

    def __str__(self):
        return f"{self.nom} ({self.type})"


class Contact(models.Model):
    """Client ou fournisseur d'une entreprise."""
    TYPE_CHOICES = [
        ('CLIENT', 'Client'),
        ('FOURNISSEUR', 'Fournisseur'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entreprise = models.ForeignKey(
        Entreprise,
        on_delete=models.CASCADE,
        related_name="contacts"
    )
    nom = models.CharField(max_length=255)
    telephone = models.CharField(max_length=30, blank=True, null=True)
    type = models.CharField(max_length=15, choices=TYPE_CHOICES)

    class Meta:
        verbose_name = "Contact"
        verbose_name_plural = "Contacts"

    def __str__(self):
        return f"{self.nom} ({self.type})"


class Plan(models.Model):
    """Offre d'abonnement proposée par Femi."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nom = models.CharField(max_length=100) # Micro, Pro, Business
    description = models.TextField(blank=True, null=True) # Description courte
    prix = models.DecimalField(max_digits=10, decimal_places=2) # 10.99, 20.99, 30.99
    est_a_partir_de = models.BooleanField(default=False, help_text="Cocher si le prix est un prix de départ ('À partir de')")
    devise = models.CharField(max_length=5, default="EUR") # EUR (€)
    duree_jours = models.PositiveIntegerField(default=30) # 30 jours (mensuel)
    populaire = models.BooleanField(default=False, help_text="Badge 'Le plus choisi'")
    fonctionnalites = models.JSONField(default=list, help_text="Liste des fonctionnalités incluses")
    
    limite_operations_mensuelles = models.PositiveIntegerField(null=True, blank=True)
    limite_utilisateurs = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "Plan d'abonnement"
        verbose_name_plural = "Plans d'abonnement"

    def __str__(self):
        prefix = "À partir de " if self.est_a_partir_de else ""
        return f"{self.nom} - {prefix}{self.prix} {self.devise}/mois"
    
class Abonnement(models.Model):
    """Souscription d'une Entreprise à un Plan précis."""
    STATUT_CHOICES = [
        ('ACTIF', 'Actif'),
        ('EXPIRE', 'Expiré'),
        ('ANNULE', 'Annulé'),
        ('EN_ATTENTE', 'En attente'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entreprise = models.ForeignKey(
        Entreprise,
        on_delete=models.CASCADE,
        related_name="abonnements"
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name="abonnements"
    )
    prix_paye = models.DecimalField(max_digits=10, decimal_places=2)
    date_debut = models.DateField()
    date_fin = models.DateField()
    statut = models.CharField(max_length=15, choices=STATUT_CHOICES, default='EN_ATTENTE')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Abonnement"
        verbose_name_plural = "Abonnements"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.entreprise.nom} - {self.plan.nom} ({self.statut})"