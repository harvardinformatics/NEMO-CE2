import datetime
from collections import defaultdict
from typing import Dict, Iterable, Set
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from NEMO.decorators import staff_member_required
from NEMO.models import (
	Customization,
	MembershipHistory,
	PhysicalAccessLevel,
	Qualification,
	QualificationLevel,
	Tool,
	ToolQualificationGroup,
	User,
	create_training_history,
)
from NEMO.typing import QuerySetType
from NEMO.utilities import EmailCategory, format_datetime, get_email_from_settings, localize, send_mail
from NEMO.views.customization import ToolCustomization
from NEMO.views.users import get_identity_service


@staff_member_required
@require_GET
def qualifications(request):
	"""Present a web page to allow staff to qualify or disqualify users on particular tools."""
	users = User.objects.filter(is_active=True)
	tools = Tool.objects.filter(visible=True)
	tool_groups = ToolQualificationGroup.objects.all()
	qualification_levels = QualificationLevel.objects.all()

	return render(
		request, "qualifications.html", {"users": users, "tools": list(tools), "tool_groups": list(tool_groups), "qualification_levels": list(qualification_levels)}
	)


@staff_member_required
@require_POST
def modify_qualifications(request):
	"""Change the tools that a set of users is qualified to use."""
	action = request.POST.get("action")
	if action != "qualify" and action != "disqualify":
		return HttpResponseBadRequest("You must specify that you are qualifying or disqualifying users.")
	users = request.POST.getlist("chosen_user[]") or request.POST.get("chosen_user") or []
	users = User.objects.in_bulk(users)
	if users == {}:
		return HttpResponseBadRequest("You must specify at least one user.")
	tools = request.POST.getlist("chosen_tool[]") or request.POST.getlist("chosen_tool") or []
	tool_groups = (
		request.POST.getlist("chosen_toolqualificationgroup[]")
		or request.POST.getlist("chosen_toolqualificationgroup")
		or []
	)
	# Add tools from tool group
	tools.extend(
		[
			tool.id
			for tool_group in ToolQualificationGroup.objects.filter(id__in=tool_groups)
			for tool in tool_group.tools.all()
		]
	)
	tools = Tool.objects.in_bulk(tools)
	if tools == {}:
		return HttpResponseBadRequest("You must specify at least one tool.")

	qualification_level_id = request.POST.get("qualification_level")
	record_qualification(request.user, action, tools.values(), users.values(), qualification_level_id)

	if request.POST.get("redirect") == "true":
		messages.success(request, "Tool qualifications were successfully modified")
		return redirect("qualifications")
	else:
		return HttpResponse()


def record_qualification(request_user: User, action: str, tools: Iterable[Tool], users: Iterable[User], qualification_level_id = None, disqualify_details = None):
	for user in users:
		original_qualifications = set(Qualification.objects.filter(user=user))
		if action == "qualify":
			if qualification_level_id is not None:
				qualification_level = QualificationLevel.objects.get(id=qualification_level_id)
			else:
				qualification_level = None
			for t in tools:
				user.add_qualification(t, qualification_level)
			original_physical_access_levels = set(user.physical_access_levels.all())
			physical_access_level_automatic_enrollment = list(
				set(
					[
						t.grant_physical_access_level_upon_qualification
						for t in tools
						if t.grant_physical_access_level_upon_qualification and t.apply_grant_access(qualification_level)
					]
				)
			)
			user.physical_access_levels.add(*physical_access_level_automatic_enrollment)
			current_physical_access_levels = set(user.physical_access_levels.all())
			added_physical_access_levels = set(current_physical_access_levels) - set(original_physical_access_levels)
			for access_level in added_physical_access_levels:
				entry = MembershipHistory()
				entry.authorizer = request_user
				entry.parent_content_object = access_level
				entry.child_content_object = user
				entry.action = entry.Action.ADDED
				entry.save()
			if get_identity_service().get("available", False):
				for t in tools:
					tool = Tool.objects.get(id=t.id)
					if tool.grant_badge_reader_access_upon_qualification and t.apply_grant_access(qualification_level):
						parameters = {
							"username": user.username,
							"domain": user.domain,
							"requested_area": tool.grant_badge_reader_access_upon_qualification,
						}
						timeout = settings.IDENTITY_SERVICE.get("timeout", 3)
						requests.put(
							urljoin(settings.IDENTITY_SERVICE["url"], "/add/"), data=parameters, timeout=timeout
						)
		elif action == "disqualify":
			user.remove_qualifications(tools)
		current_qualifications = set(Qualification.objects.filter(user=user))
		# Record the qualification changes for each tool:
		added_qualifications = current_qualifications - original_qualifications
		for qualification in added_qualifications:
			entry = MembershipHistory()
			entry.authorizer = request_user
			entry.parent_content_object = qualification.tool
			entry.child_content_object = user
			entry.action = entry.Action.ADDED
			if qualification.qualification_level:
				entry.details = qualification.qualification_level.name
			entry.save()
			create_training_history(request_user, qualification=entry, status="Qualified", qualification_level=qualification.qualification_level)
		# Updated level in qualification
		for qualification in current_qualifications.union(original_qualifications):
			for other_qualification in original_qualifications:
				if qualification.id == other_qualification.id and qualification.qualification_level_id != other_qualification.qualification_level_id:
					entry = MembershipHistory()
					entry.authorizer = request_user
					entry.parent_content_object = qualification.tool
					entry.child_content_object = user
					entry.action = entry.Action.ADDED
					if qualification.qualification_level:
						entry.details = qualification.qualification_level.name
					entry.save()
					create_training_history(request_user, qualification=entry, status="Qualified", qualification_level=qualification.qualification_level)
		# Removed qualifications
		removed_qualifications = original_qualifications - current_qualifications
		for qualification in removed_qualifications:
			entry = MembershipHistory()
			entry.authorizer = request_user
			entry.parent_content_object = qualification.tool
			entry.child_content_object = user
			entry.action = entry.Action.REMOVED
			if disqualify_details:
				entry.details = disqualify_details
			entry.save()
			create_training_history(request_user, qualification=entry, details=entry.details, status="Disqualified")


def qualify(request_user: User, tool: Tool, user: User, qualification_level_id = None):
	record_qualification(request_user, "qualify", [tool], [user], qualification_level_id)


def disqualify(request_user: User, tool: Tool, user: User, details = None):
	record_qualification(request_user, "disqualify", [tool], [user], disqualify_details=details)


@staff_member_required
@require_GET
def get_qualified_users(request):
	tool = get_object_or_404(Tool, id=request.GET.get("tool_id"))
	users = User.objects.filter(is_active=True)
	qualifications_by_tool = Qualification.objects.filter(tool=tool)
	dictionary = {"tool": tool, "users": users, "qualification_levels": QualificationLevel.objects.all(), "qualifications": qualifications_by_tool, "expanded": True}
	return render(request, "tool_control/qualified_users.html", dictionary)


@login_required
@permission_required("NEMO.trigger_timed_services", raise_exception=True)
@require_GET
def email_grant_access(request):
	return send_email_grant_access()


def send_email_grant_access():
	emails = ToolCustomization.get_list("tool_grant_access_emails")
	if emails:
		today_date = datetime.date.today()
		last_event_date_read, created = Customization.objects.get_or_create(
			name="tool_email_grant_access_since", defaults={"value": (today_date - datetime.timedelta(days=1)).isoformat()}
		)
		qualification_since = datetime.datetime.fromisoformat(last_event_date_read.value).date()
		badge_reader_users = get_granted_badge_reader_access(qualification_since, today_date)
		physical_access_users = get_granted_physical_access(qualification_since, today_date)
		if badge_reader_users or physical_access_users:
			message = "Hello,<br>\n"
			if badge_reader_users:
				message += "The following badge reader access have been granted:<br><br>\n\n"
				message += add_access_list(badge_reader_users)
			if physical_access_users:
				message += "The following physical access levels have been granted:<br><br>\n\n"
				message += add_access_list(physical_access_users)
			subject = f"Grant access - {format_datetime(today_date, 'SHORT_DATE_FORMAT')}"
			send_mail(
				subject=subject,
				content=message,
				from_email=get_email_from_settings(),
				to=emails,
				email_category=EmailCategory.TIMED_SERVICES,
			)
		last_event_date_read.value = today_date.isoformat()
		last_event_date_read.save()
	return HttpResponse()


def add_access_list(access_dict: Dict[str, Set[User]]) -> str:
	message = ""
	for access, users in access_dict.items():
		message += f"{access}:\n<ul>\n"
		for user in users:
			details = []
			if user.badge_number:
				details.append(f"badge number: {user.badge_number}")
			try:
				# Try grabbing employee id from NEMO user details to add it
				if user.details.employee_id:
					details.append(f"employee id: {user.details.employee_id}")
			except:
				pass
			message += f"<li>{user}"
			if details:
				message += " (" + ", ".join(details) + ")"
			message += "</li>\n"
		message += "</ul>\n<br><br>"
	return message


def get_granted_badge_reader_access(qualification_since: datetime.date, today_date: datetime.date) -> Dict[str, Set[User]]:
	new_qualifications: QuerySetType[Qualification] = Qualification.objects.filter(
		tool___grant_badge_reader_access_upon_qualification__isempty=False, qualified_on__gte=qualification_since,
		qualified_on__lt=today_date).prefetch_related("tool")
	badge_reader_user: Dict[str, Set[User]] = defaultdict(set)
	for qualification in new_qualifications:
		# Only add the ones that qualify with qualification level
		if qualification.tool.apply_grant_access(qualification.qualification_level):
			badge_reader_user[qualification.tool.grant_badge_reader_access_upon_qualification].add(
				qualification.user)
	return badge_reader_user


def get_granted_physical_access(qualification_since: datetime.date, today_date: datetime.date) -> Dict[str, Set[User]]:
	include_physical_access = ToolCustomization.get_bool("tool_grant_access_include_physical_access")
	if not include_physical_access:
		return {}
	# Here we rely on the membership history to figure out who was granted access
	qualification_since_datetime = localize(datetime.datetime.combine(qualification_since, datetime.datetime.min.time()))
	today_datetime = localize(datetime.datetime.combine(today_date, datetime.datetime.min.time()))
	physical_access_level_type = ContentType.objects.get_for_model(PhysicalAccessLevel)
	user_type = ContentType.objects.get_for_model(User)
	memberships: QuerySetType[MembershipHistory] = MembershipHistory.objects.filter(parent_content_type=physical_access_level_type, child_content_type=user_type, action=MembershipHistory.Action.ADDED, date__gte=qualification_since_datetime, date__lt=today_datetime)
	physical_access_to_users: Dict[str, Set[User]] = defaultdict(set)
	for membership in memberships:
		physical_access: PhysicalAccessLevel = membership.parent_content_object
		user: User = membership.child_content_object
		# Make sure the user still has that access, in case it was added but removed since
		if physical_access in user.physical_access_levels.all():
			physical_access_to_users[physical_access.name].add(user)
	return physical_access_to_users
