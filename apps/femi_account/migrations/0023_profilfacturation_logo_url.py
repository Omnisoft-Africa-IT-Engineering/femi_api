from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('femi_account', '0022_profilfacturation'),
    ]

    operations = [
        migrations.AddField(
            model_name='profilfacturation',
            name='logo_url',
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
    ]