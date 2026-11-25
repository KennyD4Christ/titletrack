from django.db import migrations, models


def backfill_registered_plots(apps, schema_editor):
    Plot = apps.get_model("tracker", "Plot")
    InventoryEvent = apps.get_model("tracker", "InventoryEvent")
    database = schema_editor.connection.alias

    registered_plot_ids = (
        InventoryEvent.objects.using(database)
        .filter(
            action="plot_registered",
            plot__isnull=False,
        )
        .values_list("plot_id", flat=True)
    )

    Plot.objects.using(database).filter(
        pk__in=registered_plot_ids,
    ).update(inventory_registered=True)


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0015_inventoryevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="plot",
            name="inventory_registered",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            backfill_registered_plots,
            migrations.RunPython.noop,
        ),
    ]
