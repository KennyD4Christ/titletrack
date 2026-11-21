from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 3 of 3 for canonical keys: the constraints. They can only be added once 0013 has filled every key,
    which is why 0013 refuses to finish while any collision exists. The older exact-match constraints stay."""

    dependencies = [
        ("tracker", "0013_backfill_canonical_keys"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="estate",
            constraint=models.UniqueConstraint(fields=("company", "name_key"), name="uniq_estate_key_per_company"),
        ),
        migrations.AddConstraint(
            model_name="estate",
            constraint=models.CheckConstraint(condition=models.Q(("name_key", ""), _negated=True),
                                              name="estate_key_not_blank"),
        ),
        migrations.AddConstraint(
            model_name="plot",
            constraint=models.UniqueConstraint(fields=("estate", "plot_key"), name="uniq_plot_key_per_estate"),
        ),
        migrations.AddConstraint(
            model_name="plot",
            constraint=models.CheckConstraint(condition=models.Q(("plot_key", ""), _negated=True),
                                              name="plot_key_not_blank"),
        ),
    ]
