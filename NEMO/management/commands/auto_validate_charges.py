from django.core.management import BaseCommand

from NEMO.views.charge_validation import do_auto_validate_charges


class Command(BaseCommand):
	help = (
		"Run every day to auto validate charges when the time limit is up."
		"Charges validation need to be enabled and an adjustment time limit set in Customization for this to work."
	)

	def handle(self, *args, **options):
		do_auto_validate_charges()
