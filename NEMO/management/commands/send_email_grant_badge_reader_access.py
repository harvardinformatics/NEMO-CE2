from django.core.management import BaseCommand

from NEMO.views.qualifications import send_email_grant_badge_reader_access


class Command(BaseCommand):
    help = (
        "Run every day to trigger the email notification to grant badge reader access."
        "The grant badge reader access email has to be set in tool customizations for this to work."
    )

    def handle(self, *args, **options):
        send_email_grant_badge_reader_access()
