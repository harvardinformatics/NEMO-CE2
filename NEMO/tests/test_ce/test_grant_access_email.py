import datetime

from django.core.management import call_command
from django.test import TestCase

from NEMO.models import Customization, EmailLog, Qualification, Tool, User
from NEMO.views.customization import ToolCustomization


class GrantAccessEmailTest(TestCase):
    def test_qualification_grant_access(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        user = User.objects.create(username="test_user", first_name="Testy", last_name="McTester", badge_number=222222)
        tool = Tool.objects.create(name="Test tool")
        # Qualify user on the tool
        Qualification.objects.create(tool=tool, user=user, qualified_on=yesterday)
        call_command("send_email_grant_badge_reader_access")
        # No emails set, nothing should be sent
        self.assertFalse(EmailLog.objects.exists())
        ToolCustomization.set("tool_grant_badge_access_emails", "abc@example.com")
        call_command("send_email_grant_badge_reader_access")
        # Manually reset the date otherwise it won't find any qualification
        Customization.objects.update_or_create(
            name="tool_email_grant_access_since", defaults={"value": yesterday.isoformat()}
        )
        # No grant access set, nothing should be sent
        self.assertFalse(EmailLog.objects.exists())
        tool.grant_badge_reader_access_upon_qualification = "Cleanroom access"
        tool.save()
        call_command("send_email_grant_badge_reader_access")
        # No grant access set, nothing should be sent
        self.assertEqual(EmailLog.objects.count(), 1)
        # Try again without resetting the customization date, nothing should happen
        call_command("send_email_grant_badge_reader_access")
        self.assertEqual(EmailLog.objects.count(), 1)
