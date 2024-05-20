from django.core.management import BaseCommand

from NEMO.views.timed_services import do_auto_cancel_training_sessions


class Command(BaseCommand):
    help = (
        "Run every minute to cancel training sessions and mark them as missed. "
        "Only applicable to training sessions with an auto cancel value."
    )

    def handle(self, *args, **options):
        do_auto_cancel_training_sessions()
