from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from NEMO.forms import ShadowingVerificationRequestForm
from NEMO.models import (
    Qualification,
    QualificationLevel,
    RequestStatus,
    ShadowingVerificationRequest,
    Tool,
    UsageEvent,
    User,
)
from NEMO.tests.test_utilities import create_user_and_project, login_as, login_as_user
from NEMO.utilities import date_input_format


class ShadowingVerificationTestCase(TestCase):
    def test_shadowing_verification_disabled_no_requests(self):
        # No tools that allow shadowing verification exist
        # No shadowing verifications exist
        # For regular user
        regular_user = create_user()
        self.check_view_edit_create_disabled(regular_user)
        # For facility manager
        facility_manager = create_user(is_facility_manager=True)
        self.check_view_edit_create_disabled(facility_manager)

    def test_shadowing_verification_enabled_and_disabled_with_requests(self):
        # Initialize data
        user_shadowed, project, tool, event_date = create_tool_and_usage()
        user = login_as_user(self.client)
        # Create at least one facility manager for approval
        facility_manager = create_user(is_facility_manager=True)
        shadowing_verification = create_shadowing_verification(
            user, tool, None, event_date, user_shadowed, RequestStatus.PENDING
        )
        # One tool that allows shadowing exists
        # One shadowing verification exist
        self.check_view_edit_create_enabled(user, shadowing_verification.id)
        # Disable shadowing verification
        tool.allow_user_shadowing_verification_request = False
        tool.save()
        # For the user who has shadowing verifications
        self.check_view_enabled_edit_create_disabled(user, shadowing_verification.id)
        # For facility manager
        self.check_view_enabled_edit_create_disabled(facility_manager, shadowing_verification.id)
        # For user without any shadowing verifications
        other_user = create_user(is_facility_manager=False)
        self.check_view_edit_create_disabled(other_user)

    def test_approve_request(self):
        user_shadowed, project, tool, event_date = create_tool_and_usage()
        user = create_user()
        shadowing_verification = create_shadowing_verification(
            user, tool, None, event_date, user_shadowed, RequestStatus.PENDING
        )
        facility_manager = create_user(is_facility_manager=True)
        data = {
            "tool": shadowing_verification.tool.id,
            "shadowed_qualified_user": shadowing_verification.shadowed_qualified_user.id,
            "event_date": shadowing_verification.event_date.strftime(date_input_format),
            "description": shadowing_verification.description,
            "approve_request": "",
        }
        # Can't approve does not have effect as a regular user (acts as edit)
        login_as(self.client, user)
        self.client.post(
            reverse("edit_shadowing_verification", args=[shadowing_verification.id]),
            data,
        )
        self.assertFalse(Qualification.objects.filter(tool=tool, user=user).exists())
        updated = ShadowingVerificationRequest.objects.get(pk=shadowing_verification.id)
        self.assertEqual(updated.reviewer, None)
        self.assertEqual(updated.status, RequestStatus.PENDING)
        # Approve success as a facility manager
        login_as(self.client, facility_manager)
        self.client.post(
            reverse("edit_shadowing_verification", args=[shadowing_verification.id]),
            data,
        )
        self.assertTrue(Qualification.objects.filter(tool=tool, user=user).exists())
        updated = ShadowingVerificationRequest.objects.get(pk=shadowing_verification.id)
        self.assertEqual(updated.reviewer, facility_manager)
        self.assertEqual(updated.status, RequestStatus.APPROVED)

    def test_deny_request(self):
        user_shadowed, project, tool, event_date = create_tool_and_usage()
        user = create_user()
        shadowing_verification = create_shadowing_verification(
            user, tool, None, event_date, user_shadowed, RequestStatus.PENDING
        )
        facility_manager = create_user(is_facility_manager=True)
        data = {
            "tool": shadowing_verification.tool.id,
            "shadowed_qualified_user": shadowing_verification.shadowed_qualified_user.id,
            "event_date": shadowing_verification.event_date.strftime(date_input_format),
            "description": shadowing_verification.description,
            "deny_request": "",
        }
        # Can't approve does not have effect as a regular user (acts as edit)
        login_as(self.client, user)
        self.client.post(
            reverse("edit_shadowing_verification", args=[shadowing_verification.id]),
            data,
            follow=True,
        )
        self.assertFalse(Qualification.objects.filter(tool=tool, user=user).exists())
        updated = ShadowingVerificationRequest.objects.get(pk=shadowing_verification.id)
        self.assertEqual(updated.reviewer, None)
        self.assertEqual(updated.status, RequestStatus.PENDING)
        # Deny success as a facility manager
        login_as(self.client, facility_manager)
        self.client.post(
            reverse("edit_shadowing_verification", args=[shadowing_verification.id]),
            data,
            follow=True,
        )
        self.assertFalse(Qualification.objects.filter(tool=tool, user=user).exists())
        updated = ShadowingVerificationRequest.objects.get(pk=shadowing_verification.id)
        self.assertEqual(updated.reviewer, facility_manager)
        self.assertEqual(updated.status, RequestStatus.DENIED)

    def test_shadowing_request_validation_with_qualification_levels(self):
        ql1 = create_qualification_level("QL1")
        ql2 = create_qualification_level("QL2")
        user_shadowed, project, tool, event_date = create_tool_and_usage([ql1])
        user = create_user()
        # Qualification level is not populated (Fail)
        form = create_form(tool, user_shadowed, event_date, user)
        print(form.errors.get_json_data())
        self.assertFalse(form.is_valid())
        self.assertTrue("qualification_level" in form.errors.get_json_data())
        # Qualification level is populated but is not allowed for tool (Fail)
        form = create_form(tool, user_shadowed, event_date, user, ql2)
        print(form.errors.get_json_data())
        self.assertFalse(form.is_valid())
        self.assertTrue("qualification_level" in form.errors.get_json_data())
        # Qualification level is populated and is allowed for tool (Pass)
        form = create_form(tool, user_shadowed, event_date, user, ql1)
        print(form.errors.get_json_data())
        self.assertTrue(form.is_valid())
        self.assertFalse("qualification_level" in form.errors.get_json_data())

    def test_shadowing_request_validation_without_qualification_levels(self):
        user_shadowed, project = create_user_and_project()
        tool = create_tool("tool_one", True, None)
        user = create_user()
        event_date = date.today()
        form = create_form(tool, user_shadowed, event_date, user)
        # Tool usage does not exist (Fail)
        self.assertFalse(form.is_valid())
        self.assertTrue("shadowed_qualified_user" in form.errors.get_json_data())
        # Tool usage exists (Pass)
        create_usage(user_shadowed, project, tool, event_date)
        form = create_form(tool, user_shadowed, event_date, user)
        self.assertTrue(form.is_valid())
        self.assertFalse("shadowed_qualified_user" in form.errors.get_json_data())
        # Tool does not allow shadowing verification (Fail)
        tool.allow_user_shadowing_verification_request = False
        tool.save()
        form = create_form(tool, user_shadowed, event_date, user)
        self.assertFalse(form.is_valid())
        self.assertTrue("tool" in form.errors.get_json_data())

    def check_view_edit_create_disabled(self, user):
        login_as(self.client, user)
        # Feature page opens and displays that the feature is not enabled
        response = self.client.get(reverse("shadowing_verifications"))
        self.assertContains(response, "This feature is not enabled.", status_code=200)
        # Create request page fails
        response = self.client.get(reverse("create_shadowing_verification"))
        self.assertEqual(response.status_code, 400)
        # Edit request page fails
        response = self.client.get(reverse("edit_shadowing_verification", args=[1]))
        self.assertEqual(response.status_code, 400)

    def check_view_enabled_edit_create_disabled(self, user, edit_id):
        login_as(self.client, user)
        # Feature page opens
        response = self.client.get(reverse("shadowing_verifications"))
        self.assertNotContains(response, "This feature is not enabled.", status_code=200)
        # Create request page fails
        response = self.client.get(reverse("create_shadowing_verification"))
        self.assertEqual(response.status_code, 400)
        # Edit request page fails
        response = self.client.get(reverse("edit_shadowing_verification", args=[edit_id]))
        self.assertEqual(response.status_code, 400)

    def check_view_edit_create_enabled(self, user, edit_id):
        login_as(self.client, user)
        # Feature page opens
        response = self.client.get(reverse("shadowing_verifications"))
        self.assertNotContains(response, "This feature is not enabled.", status_code=200)
        # Create request page opens
        response = self.client.get(reverse("create_shadowing_verification"))
        self.assertEqual(response.status_code, 200)
        # Edit request page opens
        response = self.client.get(reverse("edit_shadowing_verification", args=[edit_id]))
        self.assertEqual(response.status_code, 200)


def create_tool(name, allow_shadowing, qualification_levels):
    tool = Tool.objects.create(
        name="[Test Tool] " + name,
        _allow_user_shadowing_verification_request=allow_shadowing,
    )
    if qualification_levels:
        tool.shadowing_verification_request_qualification_levels.set(qualification_levels)
    return tool


def create_usage(user, project, tool, event_date):
    end = timezone.now()
    UsageEvent.objects.create(
        user=user,
        operator=user,
        project=project,
        tool=tool,
        start=end - timedelta(minutes=15),
        end=end,
    )


def create_user(is_facility_manager=False):
    count = User.objects.count()
    return User.objects.create(
        first_name="Testy",
        last_name="McTester",
        username=f"test{count}",
        email=f"test{count}@test.com",
        is_facility_manager=is_facility_manager,
    )


def create_qualification_level(name):
    return QualificationLevel.objects.create(
        name=name,
        qualify_user=True,
    )


def create_shadowing_verification(user, tool, qualification_level, event_date, shadowed, status):
    return ShadowingVerificationRequest.objects.create(
        creator=user,
        description="Some description",
        tool=tool,
        qualification_level=qualification_level,
        event_date=event_date,
        shadowed_qualified_user=shadowed,
        status=status,
    )


def create_tool_and_usage(qualification_levels=None):
    user, project = create_user_and_project()
    tool = create_tool("tool_one", True, qualification_levels)
    event_date = date.today()
    create_usage(user, project, tool, event_date)
    return user, project, tool, event_date


def create_form(tool, shadowed, event_date, user, qualification_level=None):
    return ShadowingVerificationRequestForm(
        {
            "tool": tool.id,
            "shadowed_qualified_user": shadowed.id,
            "qualification_level": qualification_level.id if qualification_level else None,
            "event_date": event_date.strftime(date_input_format),
            "description": "Some description",
        },
        initial={"creator": user},
    )
