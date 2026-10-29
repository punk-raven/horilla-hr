from django.db import migrations


def convert_to_rupee(apps, schema_editor):
    """Every existing company shows amounts in rupees, symbol first (₹ 1,000)."""
    PayrollSettings = apps.get_model("payroll", "PayrollSettings")
    PayrollSettings.objects.update(currency_symbol="₹", position="prefix")


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0013_payrollsettings_rupee_prefix_default"),
    ]

    operations = [
        # The previous symbols are not recorded, so there is nothing to restore.
        migrations.RunPython(convert_to_rupee, migrations.RunPython.noop),
    ]
