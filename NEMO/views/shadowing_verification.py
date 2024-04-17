from json import dumps
from typing import List, Set

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET, require_http_methods

from NEMO.decorators import staff_member_or_manager_required
from NEMO.forms import ShadowingVerificationRequestForm
from NEMO.models import Notification, QualificationLevel, RequestStatus, ShadowingVerificationRequest, Tool, User
from NEMO.utilities import (
    BasicDisplayTable,
    EmailCategory,
    bootstrap_primary_color,
    export_format_datetime,
    get_full_url,
    quiet_int,
    render_email_template,
    send_mail,
)
from NEMO.views.customization import (
    ShadowingVerificationCustomization,
    get_media_file_contents,
)
from NEMO.views.notifications import (
    create_shadowing_verification_request_notification,
    delete_notification,
    get_notifications,
)
from NEMO.views.qualifications import qualify


@login_required
@require_GET
def shadowing_verification_requests(request):
    user: User = request.user
    max_requests = quiet_int(
        ShadowingVerificationCustomization.get("shadowing_verification_request_display_max"),
        None,
    )
    shadowing_verification_request_title = ShadowingVerificationCustomization.get(
        "shadowing_verification_request_title"
    )
    shadowing_verifications = ShadowingVerificationRequest.objects.filter(deleted=False)
    my_requests = shadowing_verifications.filter(creator=user)

    user_is_reviewer = is_user_a_reviewer(user)
    user_is_staff = user.is_facility_manager or user.is_staff
    if not user_is_reviewer and not user_is_staff:
        shadowing_verifications = my_requests
    elif user_is_reviewer:
        # show all requests the user can review (+ his requests), exclude the rest
        exclude = []
        for shadowing_verification in shadowing_verifications:
            if user != shadowing_verification.creator and user not in shadowing_verification.reviewers():
                exclude.append(shadowing_verification.pk)
        shadowing_verifications = shadowing_verifications.exclude(pk__in=exclude)

    allowed_tools_exist = get_tools_and_qualification_levels().exists()
    facility_managers_exist = User.objects.filter(is_active=True, is_facility_manager=True).exists()
    reviewers_exist = (
        facility_managers_exist or Tool.objects.filter(_shadowing_verification_reviewers__isnull=False).exists()
    )
    feature_enabled = shadowing_verifications.exists() or allowed_tools_exist and reviewers_exist
    dictionary = {
        "feature_enabled": feature_enabled,
        "shadowing_verification_request_title": shadowing_verification_request_title,
    }
    if feature_enabled:
        qualification_levels_exist = QualificationLevel.objects.exists()
        dictionary.update(
            {
                "pending_requests": shadowing_verifications.filter(status=RequestStatus.PENDING)[:max_requests],
                "approved_requests": shadowing_verifications.filter(status=RequestStatus.APPROVED)[:max_requests],
                "denied_requests": shadowing_verifications.filter(status=RequestStatus.DENIED)[:max_requests],
                "feature_description": ShadowingVerificationCustomization.get(
                    "shadowing_verification_request_description"
                ),
                "request_notifications": get_notifications(
                    request.user, Notification.Types.SHADOWING_VERIFICATION_REQUEST, delete=False
                ),
                "qualification_levels_exist": qualification_levels_exist,
                "allowed_tools_exist": allowed_tools_exist,
                "table_col_number": 5 + (1 if user_is_reviewer else 0) + (1 if qualification_levels_exist else 0),
                "user_is_reviewer": user_is_reviewer,
            }
        )

    # Delete notifications for seen requests
    Notification.objects.filter(
        user=request.user,
        notification_type=Notification.Types.SHADOWING_VERIFICATION_REQUEST,
        object_id__in=my_requests,
    ).delete()
    return render(request, "shadowing_verification/shadowing_verification_requests.html", dictionary)


@login_required
@require_http_methods(["GET", "POST"])
def create_shadowing_verification_request(request, request_id=None):
    tools_and_qualifications = get_tools_and_qualification_levels()

    if not tools_and_qualifications.exists():
        return HttpResponseBadRequest("Shadowing verification requests cannot be created or edited.")

    user: User = request.user
    try:
        shadowing_verification_request = ShadowingVerificationRequest.objects.get(id=request_id)
    except ShadowingVerificationRequest.DoesNotExist:
        shadowing_verification_request = ShadowingVerificationRequest()

    dictionary = {
        "shadowing_verification_request_description_placeholder": ShadowingVerificationCustomization.get(
            "shadowing_verification_request_description_placeholder"
        ),
        "tools": get_tools_and_qualification_levels_json(tools_and_qualifications),
        "users": User.objects.all(),
    }
    if request.method == "POST":
        edit = bool(shadowing_verification_request.id)
        form = create_request_post_form(request, user, edit, shadowing_verification_request)

        if form.is_valid():
            if not edit:
                form.instance.creator = user
            if edit and user in shadowing_verification_request.reviewers():
                handle_decision(request, user, shadowing_verification_request)

            form.instance.last_updated_by = user
            shadowing_verification_request = form.save()
            handle_emails_and_notifications(request, edit, user, shadowing_verification_request)
            return redirect("shadowing_verifications")
        else:
            dictionary["form"] = form
            return render(
                request,
                "shadowing_verification/shadowing_verification_request.html",
                dictionary,
            )
    else:
        form = ShadowingVerificationRequestForm(instance=shadowing_verification_request)
        dictionary["form"] = form
        return render(
            request,
            "shadowing_verification/shadowing_verification_request.html",
            dictionary,
        )


def get_tools_and_qualification_levels():
    tools = Tool.objects.filter(_allow_user_shadowing_verification_request=True)
    if QualificationLevel.objects.exists():
        tools = tools.exclude(_shadowing_verification_request_qualification_levels=None)
    return tools


def get_tools_and_qualification_levels_json(tools):
    tool_list = []
    for tool in tools:
        tool_list.append(
            {
                "name": tool.__str__(),
                "id": tool.id,
                "qualification_levels": (
                    [
                        {
                            "name": qualification_level.__str__(),
                            "id": qualification_level.id,
                        }
                        for qualification_level in tool.shadowing_verification_request_qualification_levels.all()
                    ]
                    if tool.shadowing_verification_request_qualification_levels
                    else None
                ),
            }
        )
    return mark_safe(dumps(tool_list))


def create_request_post_form(request, user, edit, shadowing_verification_request):
    form = ShadowingVerificationRequestForm(
        request.POST,
        instance=shadowing_verification_request,
        initial={"creator": shadowing_verification_request.creator if edit else user},
    )

    # some extra validation needs to be done here because it depends on the user
    errors = []
    if edit:
        if shadowing_verification_request.deleted:
            errors.append("You are not allowed to edit deleted requests.")
        if shadowing_verification_request.status != RequestStatus.PENDING:
            errors.append("Only pending requests can be modified.")
        if shadowing_verification_request.creator != user and not user in shadowing_verification_request.reviewers():
            errors.append("You are not allowed to edit this request.")

    # add errors to the form for better display
    for error in errors:
        form.add_error(None, error)

    return form


def handle_decision(request, user, shadowing_verification_request):
    decision_option = [state for state in ["approve_request", "deny_request"] if state in request.POST]
    decision = decision_option[0] if len(decision_option) == 1 else None
    if decision:
        if decision == "approve_request":
            shadowing_verification_request.status = RequestStatus.APPROVED
            # Add qualification to user
            qualify(
                user,
                shadowing_verification_request.tool,
                shadowing_verification_request.creator,
                (
                    shadowing_verification_request.qualification_level.id
                    if shadowing_verification_request.qualification_level
                    else None
                ),
            )
        else:
            shadowing_verification_request.status = RequestStatus.DENIED
        shadowing_verification_request.reviewer = user


def handle_emails_and_notifications(request, edit, user, shadowing_verification_request):
    reviewers: Set[User] = set(shadowing_verification_request.reviewers())

    create_shadowing_verification_request_notification(shadowing_verification_request)

    if edit:
        # remove notification for current user and other facility managers
        delete_notification(
            Notification.Types.SHADOWING_VERIFICATION_REQUEST,
            shadowing_verification_request.id,
            [user],
        )
        if user in reviewers:
            delete_notification(
                Notification.Types.SHADOWING_VERIFICATION_REQUEST,
                shadowing_verification_request.id,
                reviewers,
            )
    send_request_received_email(request, shadowing_verification_request, edit, reviewers)


@login_required
@require_GET
def delete_shadowing_verification_request(request, request_id):
    shadowing_verification_request = get_object_or_404(ShadowingVerificationRequest, id=request_id)

    if shadowing_verification_request.creator != request.user:
        return HttpResponseBadRequest("You are not allowed to delete a request you didn't create.")
    if shadowing_verification_request and shadowing_verification_request.status != RequestStatus.PENDING:
        return HttpResponseBadRequest("You are not allowed to delete a request that was already completed.")

    shadowing_verification_request.deleted = True
    shadowing_verification_request.save(update_fields=["deleted"])
    delete_notification(
        Notification.Types.SHADOWING_VERIFICATION_REQUEST,
        shadowing_verification_request.id,
    )
    return redirect("shadowing_verifications")


def send_request_received_email(
    request,
    shadowing_verification_request: ShadowingVerificationRequest,
    edit,
    reviewers: Set[User],
):
    shadowing_verification_request_notification_email = get_media_file_contents(
        "shadowing_verification_notification_email.html"
    )
    if shadowing_verification_request_notification_email:
        # reviewers
        reviewer_emails = [
            email
            for user in reviewers
            for email in user.get_emails(user.get_preferences().email_send_shadowing_verification_updates)
        ]
        # cc creator
        creator_email_preferences = (
            shadowing_verification_request.creator.get_preferences().email_send_shadowing_verification_updates
        )
        creator_emails = shadowing_verification_request.creator.get_emails(creator_email_preferences)
        status = (
            "approved"
            if shadowing_verification_request.status == RequestStatus.APPROVED
            else (
                "denied"
                if shadowing_verification_request.status == RequestStatus.DENIED
                else "updated" if edit else "received"
            )
        )
        absolute_url = get_full_url(reverse("shadowing_verifications"), request)
        color_type = "success" if status == "approved" else "danger" if status == "denied" else "info"
        message = render_email_template(
            shadowing_verification_request_notification_email,
            {
                "template_color": bootstrap_primary_color(color_type),
                "shadowing_verification": shadowing_verification_request,
                "status": status,
                "shadowing_verification_url": absolute_url,
            },
        )
        if status in ["received", "updated"]:
            ccs: List[str] = creator_emails
            if status == "received":
                # Add shadowed user as cc when request is first created
                ccs.append(shadowing_verification_request.shadowed_qualified_user.email)
            send_mail(
                subject=f"Shadowing verification for the {shadowing_verification_request.tool.name} {status}",
                content=message,
                from_email=shadowing_verification_request.creator.email,
                to=reviewer_emails,
                cc=ccs,
                email_category=EmailCategory.SHADOWING_VERIFICATION_REQUESTS,
            )
        else:
            send_mail(
                subject=f"Your shadowing verification for the {shadowing_verification_request.tool.name} has been {status}",
                content=message,
                from_email=shadowing_verification_request.reviewer.email,
                to=creator_emails,
                cc=reviewer_emails,
                email_category=EmailCategory.SHADOWING_VERIFICATION_REQUESTS,
            )


@staff_member_or_manager_required
@require_GET
def csv_export(request):
    return shadowing_verification_requests_csv_export(ShadowingVerificationRequest.objects.filter(deleted=False))


def shadowing_verification_requests_csv_export(request_list: List[ShadowingVerificationRequest]) -> HttpResponse:
    table_result = BasicDisplayTable()
    table_result.add_header(("status", "Status"))
    table_result.add_header(("created_date", "Created date"))
    table_result.add_header(("last_updated", "Last updated"))
    table_result.add_header(("creator", "Creator"))
    table_result.add_header(("tool", "Tool"))
    table_result.add_header(("qualification_level", "Qualification Level"))
    table_result.add_header(("shadowed_qualified_user", "Shadowed User"))
    table_result.add_header(("event_date", "Date"))
    table_result.add_header(("reviewer", "Reviewer"))
    for req in request_list:
        req: ShadowingVerificationRequest = req
        table_result.add_row(
            {
                "status": req.get_status_display(),
                "created_date": req.creation_time,
                "last_updated": req.last_updated,
                "creator": req.creator,
                "tool": req.tool.name,
                "qualification_level": req.qualification_level.name if req.qualification_level else "",
                "shadowed_qualified_user": req.shadowed_qualified_user,
                "event_date": req.event_date,
                "reviewer": req.reviewer,
            }
        )

    filename = f"shadowing_verifications_{export_format_datetime()}.csv"
    response = table_result.to_csv()
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def is_user_a_reviewer(user: User) -> bool:
    is_reviewer_on_any_tool = Tool.objects.filter(_shadowing_verification_reviewers__in=[user]).exists()
    return user.is_facility_manager or is_reviewer_on_any_tool
