from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('femi_account', '0008_entreprise_adresse'),
    ]

    operations = [
        migrations.AddField(
            model_name='abonnement',
            name='transaction_id',
            field=models.CharField(blank=True, max_length=100, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='abonnement',
            name='payment_url',
            field=models.URLField(blank=True, max_length=500, null=True),
        ),
    ]