from django.db import migrations


# Special Migration class forcing the migration to apply to NEMO
class NEMOMigration(migrations.Migration):
    def __init__(self, name, app_label):
        super().__init__(name, "NEMO")
        self.replaces = (("NEMO", self.__class__.__module__.rsplit(".", 1)[-1]),)
