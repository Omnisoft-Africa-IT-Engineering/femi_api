import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('femi_account', '0021_devis_lignedevis'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProfilFacturation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nom_commercial', models.CharField(blank=True, default='', max_length=255)),
                ('telephone_pro', models.CharField(blank=True, default='', max_length=50)),
                ('adresse', models.CharField(blank=True, max_length=255, null=True)),
                ('nif_rccm', models.CharField(blank=True, max_length=150, null=True)),
                ('moyens_paiement', models.TextField(blank=True, default='')),
                ('conditions_defaut', models.CharField(blank=True, max_length=255, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('entreprise', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='profil_facturation', to='femi_account.entreprise')),
            ],
            options={
                'verbose_name': 'Profil de facturation',
                'verbose_name_plural': 'Profils de facturation',
            },
        ),
    ]