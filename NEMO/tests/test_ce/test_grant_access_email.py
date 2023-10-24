import datetime

from django.core.management import call_command
from django.test import TestCase

from NEMO.models import (
    Area,
    Customization,
    EmailLog,
    MembershipHistory,
    PhysicalAccessLevel,
    Qualification,
    QualificationLevel,
    Tool,
    User,
)
from NEMO.utilities import localize
from NEMO.views.customization import ToolCustomization
from NEMO.views.qualifications import record_qualification


class GrantAccessEmailTest(TestCase):
    def test_qualification_grant_badge_reader_access(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        user = User.objects.create(username="test_user", first_name="Testy", last_name="McTester", badge_number=222222)
        tool = Tool.objects.create(name="Test tool")
        level_24_7 = QualificationLevel.objects.create(name="24/7")
        level_intro = QualificationLevel.objects.create(name="Introduction")
        clean_and_record(yesterday, user, tool, level_intro)
        call_command("send_email_grant_access")
        # No emails set, nothing should be sent
        self.assertFalse(EmailLog.objects.exists())
        ToolCustomization.set("tool_grant_access_emails", "abc@example.com")
        # Manually reset the date otherwise it won't find any qualification
        Customization.objects.update_or_create(
            name="tool_email_grant_access_since", defaults={"value": yesterday.isoformat()}
        )
        call_command("send_email_grant_access")
        # No grant access set, nothing should be sent
        self.assertFalse(EmailLog.objects.exists())
        # Set grant access triggered on different level, nothing should be sent
        tool.grant_badge_reader_access_upon_qualification = "Cleanroom access"
        tool.grant_access_for_qualification_levels.set([level_24_7])
        tool.save()
        Customization.objects.update_or_create(
            name="tool_email_grant_access_since", defaults={"value": yesterday.isoformat()}
        )
        call_command("send_email_grant_access")
        self.assertFalse(EmailLog.objects.exists())
        # Set it to the right one
        tool.grant_access_for_qualification_levels.set([level_intro])
        tool.save()
        # Now it should work
        Customization.objects.update_or_create(
            name="tool_email_grant_access_since", defaults={"value": yesterday.isoformat()}
        )
        call_command("send_email_grant_access")
        self.assertEqual(EmailLog.objects.count(), 1)
        # Try again without resetting the customization date, should only have one email still
        call_command("send_email_grant_access")
        self.assertEqual(EmailLog.objects.count(), 1)

    def test_qualification_grant_physical_access(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        user = User.objects.create(username="test_user", first_name="Testy", last_name="McTester", badge_number=222222)
        tool = Tool.objects.create(name="Test tool")
        area = Area.objects.create(name="Cleanroom")
        physical_access_level = PhysicalAccessLevel.objects.create(
            name="Cleanroom access", area=area, schedule=PhysicalAccessLevel.Schedule.ALWAYS
        )
        level_24_7 = QualificationLevel.objects.create(name="24/7", qualify_user=True)
        level_intro = QualificationLevel.objects.create(name="Introduction", qualify_user=False)
        clean_and_record(yesterday, user, tool, level_intro)
        call_command("send_email_grant_access")
        # No emails set, nothing should be sent
        self.assertFalse(EmailLog.objects.exists())
        ToolCustomization.set("tool_grant_access_emails", "abc@example.com")
        ToolCustomization.set("tool_grant_access_include_physical_access", "enabled")
        # Manually reset the date otherwise it won't find any qualification
        Customization.objects.update_or_create(
            name="tool_email_grant_access_since", defaults={"value": yesterday.isoformat()}
        )
        clean_and_record(yesterday, user, tool, level_intro)
        call_command("send_email_grant_access")
        # No grant access set, nothing should be sent
        self.assertFalse(EmailLog.objects.exists())
        # Set grant access triggered on different level, nothing should be sent
        tool.grant_physical_access_level_upon_qualification = physical_access_level
        tool.grant_access_for_qualification_levels.set([level_24_7])
        tool.save()
        clean_and_record(yesterday, user, tool, level_intro)
        # Manually reset the date otherwise it won't find any qualification
        Customization.objects.update_or_create(
            name="tool_email_grant_access_since", defaults={"value": yesterday.isoformat()}
        )
        call_command("send_email_grant_access")
        self.assertFalse(EmailLog.objects.exists())
        # Set it to the right one
        tool.grant_access_for_qualification_levels.set([level_intro])
        tool.save()
        clean_and_record(yesterday, user, tool, level_intro)
        # Pretend the access was later removed, email should not be sent
        user.physical_access_levels.set([])
        # Manually reset the date otherwise it won't find any qualification
        Customization.objects.update_or_create(
            name="tool_email_grant_access_since", defaults={"value": yesterday.isoformat()}
        )
        call_command("send_email_grant_access")
        self.assertFalse(EmailLog.objects.exists())
        # Now it should work
        clean_and_record(yesterday, user, tool, level_intro)
        # Manually reset the date otherwise it won't find any qualification
        Customization.objects.update_or_create(
            name="tool_email_grant_access_since", defaults={"value": yesterday.isoformat()}
        )
        # Now it should work
        call_command("send_email_grant_access")
        self.assertEqual(EmailLog.objects.count(), 1)
        # Try again without resetting the customization date, should only have one email still
        call_command("send_email_grant_access")
        self.assertEqual(EmailLog.objects.count(), 1)


def clean_and_record(yesterday, user, tool, level):
    MembershipHistory.objects.all().delete()
    Qualification.objects.all().delete()
    record_qualification(user, "qualify", [tool], [user], level.id)
    # This is setting memberships and qualification date to today, so change them to yesterday, so it can trigger our email
    qualification = Qualification.objects.first()
    qualification.qualified_on = yesterday
    qualification.save()
    for membership in MembershipHistory.objects.all():
        membership.date = localize(datetime.datetime.combine(yesterday, datetime.datetime.min.time()))
        membership.save()
