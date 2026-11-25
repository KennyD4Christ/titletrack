from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 1 of 3 for canonical keys. Only adds the two columns (empty for every existing row). The data step
    (0013) and the constraints (0014) are separate migrations, so no ALTER TABLE follows an UPDATE in one
    transaction (PostgreSQL can refuse that).

    The columns carry a database default of '' so that the previous release, which does not know about them, can
    still create estates and plots if a later step stops this deployment part-way (for example 0013 refusing a
    collision). Rows it creates then have a blank key, which 0013 fills when it is run again."""

    dependencies = [
        ("tracker", "0011_obligationtype_is_current"),
    ]

    operations = [
        migrations.AddField(
            model_name="estate",
            name="name_key",
            field=models.CharField(blank=True, db_default="", default="", editable=False, max_length=600),
        ),
        migrations.AddField(
            model_name="plot",
            name="plot_key",
            field=models.CharField(blank=True, db_default="", default="", editable=False, max_length=150),
        ),
    ]
