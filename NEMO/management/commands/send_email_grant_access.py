from django.core.management import BaseCommand

from NEMO.views.timed_services import send_email_grant_access


class Command(BaseCommand):
    help = (
        "Run every day to trigger the email notification to grant access for badge reader and/or physical access."
        "The grant access emails field has to be set in tool customizations for this to work."
    )

    def handle(self, *args, **options):
        send_email_grant_access()
