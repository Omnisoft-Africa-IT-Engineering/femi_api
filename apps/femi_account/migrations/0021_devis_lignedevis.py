import apps.femi_account.models
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('femi_account', '0020_utilisateur_poste_activation'),
    ]

    operations = [
        migrations.CreateModel(
            name='Devis',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference', models.CharField(default=apps.femi_account.models.generer_reference_devis, editable=False, max_length=50, unique=True)),
                ('date_emission', models.DateField(default=django.utils.timezone.localdate)),
                ('date_validite', models.DateField(blank=True, null=True)),
                ('montant_total', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
                ('statut', models.CharField(choices=[('BROUILLON', 'Brouillon'), ('VALIDE', 'Validé'), ('FACTURE', 'Transformé en facture'), ('ANNULE', 'Annulé')], default='BROUILLON', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('client', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='devis', to='femi_account.contact')),
                ('entreprise', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='devis', to='femi_account.entreprise')),
            ],
            options={
                'verbose_name': 'Devis',
                'verbose_name_plural': 'Devis',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['entreprise', '-created_at'], name='devis_entreprise_created_idx')],
            },
        ),
        migrations.CreateModel(
            name='LigneDevis',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.TextField()),
                ('quantite', models.DecimalField(decimal_places=2, default=Decimal('1.00'), max_digits=10)),
                ('prix_unitaire', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
                ('montant_total', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14)),
                ('devis', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lignes', to='femi_account.devis')),
            ],
            options={
                'verbose_name': 'Ligne de devis',
                'verbose_name_plural': 'Lignes de devis',
                'ordering': ['id'],
            },
        ),
    ]
