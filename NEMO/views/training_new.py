import datetime
from collections import defaultdict
from datetime import timedelta
from typing import Any, List, Set, Tuple

from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Case, CharField, F, Q, Value, When
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from NEMO.decorators import any_staff_or_trainer
from NEMO.forms import TrainingEventForm, TrainingRequestForm, TrainingRequestTimeForm
from NEMO.models import (
    Notification,
    Reservation,
    ScheduledOutage,
    Tool,
    ToolTrainingDetail,
    TrainingEvent,
    TrainingHistory,
    TrainingInvitation,
    TrainingRequest,
    TrainingRequestStatus,
    TrainingRequestTime,
    User,
)
from NEMO.policy import policy_class as policy
from NEMO.utilities import (
    BasicDisplayTable,
    EmailCategory,
    create_ics,
    distinct_qs_value_list,
    export_format_datetime,
    format_datetime,
    is_trainer,
    parse_parameter_string,
    queryset_search_filter,
    render_email_template,
    send_mail,
)
from NEMO.views.constants import SYSTEM_USER_DISPLAY
from NEMO.views.customization import TrainingCustomization, get_media_file_contents
from NEMO.views.notifications import delete_notification
from NEMO.views.pagination import SortedPaginator
from NEMO.views.training import get_training_dictionary


@login_required
@require_GET
def requests(request):
    mark_training_objects_expired()
    if not TrainingCustomization.get_bool("training_module_enabled"):
        return redirect("landing")
    user: User = request.user
    training_requests = TrainingRequest.objects.filter(
        user=user, status__in=[TrainingRequestStatus.SENT, TrainingRequestStatus.REVIEWED]
    )
    training_invitations = TrainingInvitation.objects.filter(
        user=user,
        status__in=[TrainingRequestStatus.SENT, TrainingRequestStatus.REVIEWED, TrainingRequestStatus.ACCEPTED],
        training_event__start__gte=timezone.now(),
    )
    dictionary = {"training_invitations": training_invitations, "training_requests": training_requests}
    return render(request, "training_new/training_requests/training_requests.html", dictionary)


@login_required
@require_http_methods(["GET", "POST"])
def create_request(request, tool_id=None):
    training_request_form = TrainingRequestForm(request.POST or None)
    selected_tool = Tool.objects.filter(pk=training_request_form.data.get("tool") or tool_id).first()
    time_forms = []

    try:
        tool_training_details = ToolTrainingDetail.objects.get(tool_id=selected_tool)
    except ToolTrainingDetail.DoesNotExist:
        tool_training_details = None
    user_availability_allowed = (
        tool_training_details.user_availability_allowed
        if tool_training_details
        else TrainingCustomization.get_bool("training_request_default_availability_allowed")
    )
    dictionary = {
        "selected_tool": selected_tool,
        "form": training_request_form,
        "training_details": tool_training_details,
        "user_availability_allowed": user_availability_allowed,
    }
    if request.method == "POST":
        training_request_form.instance.creator = request.user
        training_request_form.instance.user = request.user
        if user_availability_allowed:
            for param in request.POST.getlist("times"):
                time_forms.append(
                    TrainingRequestTimeForm(
                        {"start_time": request.POST.get(f"start_{param}"), "end_time": request.POST.get(f"end_{param}")}
                    )
                )
        dictionary["time_forms"] = time_forms
        if training_request_form.is_valid() and all(time_form.is_valid() for time_form in time_forms):
            with transaction.atomic():
                training_request: TrainingRequest = training_request_form.save()
                # Now we can save the times
                training_request.replace_times([time_form.instance for time_form in time_forms])
                send_email_training_request_received(training_request, request)
                return redirect("training_requests") if request.device == "mobile" else HttpResponse()
    for requested_time in TrainingRequestTime.objects.filter(training_request=training_request_form.instance):
        time_forms.append(TrainingRequestTimeForm(instance=requested_time))
    dictionary["time_forms"] = time_forms
    return render(request, "training_new/training_requests/create_request.html", dictionary)


@require_GET
@login_required
def withdraw_request(request, training_request_id):
    training_request = get_object_or_404(TrainingRequest, pk=training_request_id)
    if not request.user.is_staff and not request.user == training_request.creator:
        return HttpResponseBadRequest("You are not allowed to withdraw a request you didn't create")
    training_request.withdraw(request.user)
    return redirect("training_requests")


@require_GET
@any_staff_or_trainer
def review_request(request, training_request_id):
    training_request = get_object_or_404(TrainingRequest, pk=training_request_id)
    training_request.review(request.user)
    return redirect("schedule_training_events")


@require_POST
@any_staff_or_trainer
def decline_request(request, training_request_id):
    training_request = get_object_or_404(TrainingRequest, pk=training_request_id)
    try:
        reason = parse_parameter_string(request.POST, "reason", maximum_length=200, raise_on_error=True)
    except Exception as e:
        return HttpResponseBadRequest(str(e))
    if not reason:
        return HttpResponseBadRequest("You must provide a reason when declining someone else's training request.")
    training_request.decline(request.user, reason)
    return redirect("schedule_training_events")


@login_required
@require_GET
def history(request):
    mark_training_objects_expired()
    user: User = request.user
    # Action mode is either True (actions) or False (history)
    action_mode = request.GET.get("action_mode")
    if action_mode is not None:
        request.session["training_history_mode"] = bool(action_mode == "True")
    try:
        action_mode = request.session["training_history_mode"]
    except KeyError:
        action_mode = is_trainer(user)
    managed_users = user.managed_users()
    selected_user = request.GET.get("selected_user")
    user = User.objects.get(pk=selected_user) if selected_user else user
    if user not in [request.user] + managed_users:
        return HttpResponseBadRequest("You are not allowed to see this user's history")
    # Filter out actions/training histories related to the current user.
    # Actions can be either:
    # 1. training history items the user acted on
    user_actions_filter = Q(user=user)
    # 2. training invitations created by the user
    user_actions_filter |= Q(training_invitation__creator=user)
    # 3. training sessions the user is a trainer for
    user_actions_filter |= Q(training_event__trainer=user)
    # History can be either:
    # 1. training request for the user
    user_history_filter = Q(training_request__user=user)
    # 2. training invitations for the user
    user_history_filter |= Q(training_invitation__user=user)
    # 3. training sessions the user is attending
    user_history_filter |= Q(training_event__users__in=[user])
    # 4. qualifications for the user
    user_type = ContentType.objects.get_for_model(user)
    user_history_filter |= Q(qualification__child_content_type=user_type) & Q(qualification__child_object_id=user.id)
    training_histories = TrainingHistory.objects.filter(user_actions_filter if action_mode else user_history_filter)

    # Annotate with useful attributes
    training_histories = training_histories.annotate(
        type_order=Case(
            When(training_request__isnull=False, then=Value("Request")),
            When(training_invitation__isnull=False, then=Value("Invitation")),
            When(training_event__isnull=False, then=Value("Training session")),
            When(qualification__isnull=False, then=Value("Qualification")),
            default=Value(""),
            output_field=CharField(),
        )
    )
    training_histories = training_histories.annotate(
        tool_order=Case(
            When(training_request__isnull=False, then=F("training_request__tool")),
            When(training_invitation__isnull=False, then=F("training_invitation__training_event__tool")),
            When(training_event__isnull=False, then=F("training_event__tool")),
            When(qualification__isnull=False, then=F("qualification__parent_object_id")),
            default=Value(""),
            output_field=CharField(),
        )
    )
    page = SortedPaginator(training_histories, request, order_by="-time").get_current_page()

    if bool(request.GET.get("csv", False)):
        return export_training_history(user, training_histories)

    return render(
        request,
        "training_new/training_history.html",
        {
            "page": page,
            "managed_users": managed_users,
            "selected_user": user,
            "SYSTEM_USER_DISPLAY": SYSTEM_USER_DISPLAY,
            "action_mode": action_mode,
        },
    )


def export_training_history(user, training_histories) -> HttpResponse:
    export_history = BasicDisplayTable()
    export_history.headers = [
        ("time", "Time"),
        ("type", "Type"),
        ("tool", "Tool"),
        ("dates", "Date(s)"),
        ("status", "Status"),
        ("by", "By"),
        ("details", "Details"),
    ]
    for training_history in training_histories:
        training_history: TrainingHistory = training_history
        export_history.add_row(
            {
                "time": format_datetime(training_history.time, df="SHORT_DATETIME_FORMAT"),
                "type": training_history.type_order,
                "tool": training_history.tool,
                "dates": ", ".join(
                    [
                        format_datetime(training_date)
                        if isinstance(training_date, datetime.datetime)
                        else str(training_date)
                        for training_date in training_history.dates
                    ]
                ),
                "status": training_history.status,
                "by": training_history.user,
                "details": training_history.details,
            }
        )
    name = slugify(user).replace("-", "_")
    response = export_history.to_csv()
    filename = f"{name}_training_history_{export_format_datetime()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_GET
def upcoming_events(request):
    mark_training_objects_expired()
    date_now = timezone.now()
    training_events = TrainingEvent.objects.filter(cancelled=False, end__gte=date_now).exclude(start__lte=date_now)
    dictionary = {"training_events": training_events}
    return render(request, "training_new/training_events/upcoming_training_events.html", dictionary)


@any_staff_or_trainer
@require_GET
def schedule_events(request):
    mark_training_objects_expired()
    training_requests = TrainingRequest.objects.filter(
        status__in=[TrainingRequestStatus.SENT, TrainingRequestStatus.REVIEWED]
    )
    return render(
        request,
        "training_new/training_events/schedule_training_events.html",
        {"training_requests": training_requests},
    )


@any_staff_or_trainer
@require_http_methods(["GET", "POST"])
def create_event(request, tool_id=None, training_event_id=None, request_time_id=None, training_request_id=None):
    selected_tool = Tool.objects.filter(pk=request.POST.get("tool") or tool_id).first()

    try:
        if request_time_id:
            training_request_id = TrainingRequestTime.objects.get(id=request_time_id).training_request.id
        training_request = TrainingRequest.objects.get(id=training_request_id)
        selected_tool = training_request.tool
    except (TrainingRequest.DoesNotExist, TrainingRequestTime.DoesNotExist):
        training_request = None

    try:
        training_event = TrainingEvent.objects.get(id=training_event_id)
        if training_event and training_event.end < timezone.now():
            return HttpResponseBadRequest("You cannot edit past training sessions")
        selected_tool = training_event.tool
    except TrainingEvent.DoesNotExist:
        training_event = None

    try:
        tool_training_details = ToolTrainingDetail.objects.get(tool_id=selected_tool)
    except ToolTrainingDetail.DoesNotExist:
        tool_training_details = None
    submitted_user_ids_to_invite = set(int(param) for param in request.POST.getlist("user_ids_to_invite", []) if param)
    invited_user_ids = (
        distinct_qs_value_list(training_event.pending_invitations(), "user_id") if training_event else set()
    )
    if training_event:
        initial = {"duration": training_event.duration}
    else:
        initial = {
            "duration": tool_training_details.duration
            if tool_training_details and tool_training_details.duration
            else TrainingCustomization.get_int("training_event_default_duration"),
            "capacity": tool_training_details.capacity
            if tool_training_details and tool_training_details.capacity
            else TrainingCustomization.get_int("training_event_default_capacity"),
        }
    if training_request:
        invited_user_ids.add(training_request.user_id)
        if request_time_id:
            request_start = TrainingRequestTime.objects.get(id=request_time_id)
            if request_start:
                initial["start"] = request_start.start_time
    training_event_form = TrainingEventForm(request.POST or None, instance=training_event, initial=initial)
    invited_users = set(User.objects.in_bulk(submitted_user_ids_to_invite or invited_user_ids).values())
    invalid_times = invalid_times_for_training(selected_tool, timezone.now(), timezone.now() + timedelta(weeks=3))
    dictionary = {
        "selected_tool": selected_tool,
        "form": training_event_form,
        "training_details": tool_training_details,
        "invited_users": invited_users,
        "suggested_users": suggested_users_to_invite(selected_tool).difference(invited_users),
        "suggested_times": suggested_times_for_training(selected_tool, timedelta(minutes=(initial["duration"] or 0))),
        "invalid_times": invalid_times,
    }
    if request.method == "POST":
        if not training_event_form.instance.id:
            training_event_form.instance.creator = request.user
            training_event_form.instance.trainer = request.user
        if training_event_form.is_valid():
            duration = training_event_form.cleaned_data["duration"]
            training_event_form.instance.end = training_event_form.cleaned_data["start"] + timedelta(minutes=duration)
            policy_problems = policy.check_to_create_training(training_event_form.instance)
            if policy_problems:
                training_event_form.add_error(NON_FIELD_ERRORS, [policy_problem for policy_problem in policy_problems])
            else:
                old_training_event = TrainingEvent.objects.filter(pk=training_event_form.instance.id).first()
                training_event: TrainingEvent = training_event_form.save()
                if training_event_form.instance.id and training_event.users.exists():
                    # This is an edit with attendees, check for changes in dates
                    if training_event.start != old_training_event.start or training_event.end != old_training_event.end:
                        # Sent to trainer then to all users
                        send_ics(training_event, None, updated=True)
                        for user in training_event.users.all():
                            send_ics(training_event, user, updated=True)
                user_ids_to_add = submitted_user_ids_to_invite.difference(invited_user_ids)
                user_ids_to_remove = invited_user_ids.difference(submitted_user_ids_to_invite)
                # Invite new people
                try:
                    training_event.invite_users(request.user, User.objects.filter(id__in=user_ids_to_add), request)
                    # Delete invitations to people who have been removed
                    training_event.uninvite_users(request.user, User.objects.filter(id__in=user_ids_to_remove))
                    return redirect("manage_training_events") if request.device == "mobile" else HttpResponse()
                except ValidationError as e:
                    training_event_form.add_error(NON_FIELD_ERRORS, e.messages)
    return render(request, "training_new/training_events/create_event.html", dictionary)


@any_staff_or_trainer
@require_GET
def manage_events(request):
    mark_training_objects_expired()
    user: User = request.user
    date_now = timezone.now()
    past_training_events = TrainingEvent.objects.filter(creator=user, cancelled=False).filter(Q(end__lte=date_now)|Q(start__lte=date_now, end__gte=date_now))
    training_events = TrainingEvent.objects.filter(creator=user, cancelled=False, end__gte=date_now).exclude(start__lte=date_now)
    dictionary = {"training_events": training_events, "past_training_events": past_training_events, "now": date_now}
    return render(request, "training_new/training_events/manage_training_events.html", dictionary)


@any_staff_or_trainer
@require_GET
def record_events(request, training_event_id=None):
    mark_training_objects_expired()
    dictionary = get_training_dictionary(request)
    dictionary["training_event"] = TrainingEvent.objects.filter(id=training_event_id).first()
    return render(request, "training_new/training_sessions.html", dictionary)


@any_staff_or_trainer
@require_POST
def cancel_training(request, training_event_id):
    training = get_object_or_404(TrainingEvent, id=training_event_id, cancelled=False)
    if training.users.exists() and training.end < timezone.now():
        return HttpResponseBadRequest("You cannot cancel past training sessions")
    if not request.user.is_staff and not request.user == training.trainer:
        return HttpResponseBadRequest("You are not allowed to cancel this training")
    try:
        reason = parse_parameter_string(request.POST, "reason", maximum_length=200, raise_on_error=True)
    except Exception as e:
        return HttpResponseBadRequest(str(e))
    if not reason and training.users.exists():
        return HttpResponseBadRequest(
            "You must provide a reason when cancelling an event with users already signed up."
        )
    training.cancel(request.user, reason, request)
    if request.device == "desktop":
        return HttpResponse()
    if request.device == "mobile":
        dictionary = {"event_type": "Training event", "tool": training.tool}
        return render(request, "mobile/cancellation_result.html", dictionary)


@login_required
@require_POST
def register_for_training(request, training_event_id):
    user: User = request.user
    training_event = get_object_or_404(TrainingEvent, id=training_event_id, cancelled=False)
    # Create new training invitation
    invitation = TrainingInvitation()
    invitation.training_event = training_event
    invitation.user = user
    invitation.creator = user
    # Check for policy issues
    policy_problem = policy.check_to_register_for_training(invitation)
    if policy_problem:
        return HttpResponseBadRequest(policy_problem)
    else:
        invitation.save()
        invitation.accept_invitation(user)
    return HttpResponse()


@login_required
@require_POST
def decline_invitation(request, training_invitation_id):
    training_invite: TrainingInvitation = get_object_or_404(TrainingInvitation, pk=training_invitation_id)
    if training_invite.user != request.user:
        return HttpResponseBadRequest("You cannot decline an invitation that isn't yours")
    try:
        reason = parse_parameter_string(request.POST, "reason", maximum_length=200, raise_on_error=True)
    except Exception as e:
        return HttpResponseBadRequest(str(e))
    training_invite.decline_invitation(request.user, reason, request)
    return HttpResponse()


@login_required
@require_POST
def accept_invitation(request, training_invitation_id):
    training_invite: TrainingInvitation = get_object_or_404(TrainingInvitation, id=training_invitation_id)
    if training_invite.user != request.user:
        return HttpResponseBadRequest("You cannot accept an invitation that isn't yours")
    training_invite.accept_invitation(request.user)
    return HttpResponse()


@login_required
@require_GET
def review_invitation(request, training_invitation_id):
    training_invite: TrainingInvitation = get_object_or_404(TrainingInvitation, id=training_invitation_id)
    if training_invite.user != request.user:
        return HttpResponseBadRequest("You cannot review an invitation that isn't yours")
    training_invite.review_invitation(request.user)
    return HttpResponse()


@login_required
@require_GET
def tool_training_search(request):
    return queryset_search_filter(Tool.objects.filter(visible=True), ["name"], request)


@any_staff_or_trainer
@require_GET
def user_for_training_search(request):
    return queryset_search_filter(
        User.objects.all().exclude(id=request.user.id), ["first_name", "last_name", "username"], request
    )


def mark_training_objects_expired():
    date_now = timezone.now()
    # Expire pending invitation when the training already started and remove notifications
    for t_invite in TrainingInvitation.objects.filter(
        status__in=[TrainingRequestStatus.SENT, TrainingRequestStatus.REVIEWED], training_event__start__lte=date_now
    ):
        t_invite.save_status(TrainingRequestStatus.EXPIRED, None, "Automatically expired, training session already took place")
        delete_notification(Notification.Types.TRAINING_INVITATION, t_invite.id)
        # Expire corresponding requests
        for training_request in TrainingRequest.objects.filter(tool=t_invite.tool, status=TrainingRequestStatus.INVITED, user=t_invite.user):
            training_request.save_status(TrainingRequestStatus.EXPIRED, None, "The associated invitation expired")


def send_email_training_request_received(training_request: TrainingRequest, request=None):
    message = get_media_file_contents("training_request_submitted_email.html")
    if message:
        content = render_email_template(message, {"training_request": training_request}, request)
        subject = f"New training request for the {training_request.tool.name}"
        recipients = [
            email
            for trainer in training_request.tool.trainers()
            for email in trainer.get_emails(trainer.get_preferences().email_send_training_emails)
        ]
        send_mail(
            subject=subject,
            content=content,
            to=recipients,
            from_email=training_request.user.email,
            email_category=EmailCategory.TRAINING,
        )


def send_email_training_invitation_received(training_invitation: TrainingInvitation, request=None):
    message = get_media_file_contents("training_invitation_received_email.html")
    if message:
        content = render_email_template(message, {"training_invitation": training_invitation}, request)
        subject = f"Training invitation for the {training_invitation.tool.name}{(' ('+ training_invitation.technique.name +')') if training_invitation.technique else ''}"
        training_invitation.user.email_user(
            subject=subject,
            message=content,
            from_email=training_invitation.trainer.email,
            email_notification=training_invitation.user.get_preferences().email_send_training_emails,
            email_category=EmailCategory.TRAINING,
        )


def send_email_training_invitation_declined(training_invitation: TrainingInvitation, reason=None, request=None):
    message = get_media_file_contents("training_invitation_declined_email.html")
    if message:
        content = render_email_template(
            message, {"training_invitation": training_invitation, "reason": reason}, request
        )
        subject = f"Your invitation for the {training_invitation.tool} training session was declined"
        training_invitation.user.email_user(
            subject=subject,
            message=content,
            from_email=training_invitation.user.email,
            email_notification=training_invitation.trainer.get_preferences().email_send_training_emails,
            email_category=EmailCategory.TRAINING,
        )


def send_email_training_session_cancelled(training_event: TrainingEvent, request=None):
    message = get_media_file_contents("training_session_cancelled_email.html")
    sent_to_trainer = False
    for user in training_event.users.all():
        # Add ics for cancelled event in the same email
        attachments = []
        if user and user.get_preferences().attach_cancelled_reservation:
            event_name = f"{training_event.tool.name} Training"
            attachments = [
                create_ics(
                    training_event.id,
                    event_name,
                    training_event.start,
                    training_event.end,
                    user,
                    organizer=training_event.trainer,
                    cancelled=True,
                )
            ]
        if message:
            content = render_email_template(message, {"training_session": training_event, "user": user}, request)
            subject = f"The {training_event.tool} training session was cancelled"
            user.email_user(
                subject=subject,
                message=content,
                from_email=training_event.trainer.email,
                email_notification=user.get_preferences().email_send_training_emails,
                email_category=EmailCategory.TRAINING,
                attachments=attachments,
            )
        elif attachments:
            send_ics(training_event, user, cancelled=True)
            sent_to_trainer = True
    if not sent_to_trainer:
        # Send to trainer only as he didn't get it previously
        send_ics(training_event, None, cancelled=True)


def suggested_users_to_invite(tool: Tool) -> Set[User]:
    pending_requests = TrainingRequest.objects.filter(tool=tool)
    pending_requests = pending_requests.filter(status__in=[TrainingRequestStatus.SENT, TrainingRequestStatus.REVIEWED])
    return set(User.objects.in_bulk(distinct_qs_value_list(pending_requests, "user")).values())


def invalid_times_for_training(tool: Tool, range_start, range_end, training_id=None):
    ranges = []
    coincident_trainings = TrainingEvent.objects.filter(tool=tool, cancelled=False).exclude(id=training_id)
    coincident_trainings = coincident_trainings.exclude(start__lt=range_start, end__lte=range_start)
    coincident_trainings = coincident_trainings.exclude(start__gte=range_end, end__gt=range_end)
    ranges.extend([(tr.start, tr.end) for tr in coincident_trainings])

    coincident_reservations = Reservation.objects.filter(tool=tool, cancelled=False, missed=False, shortened=False)
    coincident_reservations = coincident_reservations.exclude(start__lt=range_start, end__lte=range_start)
    coincident_reservations = coincident_reservations.exclude(start__gte=range_end, end__gt=range_end)
    ranges.extend([(res.start, res.end) for res in coincident_reservations])

    coincident_outages = ScheduledOutage.objects.filter(Q(tool=tool) | Q(resource__fully_dependent_tools__in=[tool]))
    coincident_outages = coincident_outages.exclude(start__lt=range_start, end__lte=range_start)
    coincident_outages = coincident_outages.exclude(start__gte=range_end, end__gt=range_end)
    ranges.extend([(out.start, out.end) for out in coincident_outages])

    return ranges


def suggested_times_for_training(tool, duration) -> List[Tuple[Any, Set[User]]]:
    pending_requests = TrainingRequest.objects.filter(
        tool=tool, status__in=[TrainingRequestStatus.SENT, TrainingRequestStatus.REVIEWED]
    )
    # Create a defaultdict to set the number of user times overlapping
    overlaps = defaultdict(set)

    # Loop through each user's availability
    for training_request in pending_requests:
        # Loop through each datetime range in the availability
        for available_time in training_request.trainingrequesttime_set.all():
            # Loop through each other user's availability
            overlaps[(available_time.start_time, available_time.end_time)].add(training_request.user.get_name())
            for other_training_request in pending_requests.exclude(user=training_request.user):
                # Check if the other user has any overlapping datetime ranges
                if other_training_request.trainingrequesttime_set.exists():
                    for other_available_time in other_training_request.trainingrequesttime_set.all():
                        overlap_start = max(available_time.start_time, other_available_time.start_time)
                        overlap_end = min(available_time.end_time, other_available_time.end_time)
                        overlap_duration = overlap_end - overlap_start
                        # If the overlap duration is greater than or equal to the desired duration, add user to the overlap dict
                        if overlap_duration >= duration:
                            overlaps[(overlap_start, overlap_end)].add(other_training_request.user.get_name())

    # Sort by highest count first
    sorted_tuple_list = sorted(overlaps.items(), key=lambda item: len(item[1]), reverse=True)
    # Only return results where more than one user is available at the same time
    return [item for item in sorted_tuple_list if len(item[1]) > 1]


def send_ics(training: TrainingEvent, user, cancelled=False, updated=False):
    event_name = f"{training.tool.name} Training"
    trainer = training.trainer
    if (training.users.count() == 1 or cancelled) and not updated or (updated and not user):
        # Send to trainer when first invitation is accepted or when training is cancelled
        if should_send_ics(trainer, cancelled):
            ics = create_ics(training.id, event_name, training.start, training.end, trainer, cancelled=cancelled)
            trainer.email_user(
                subject=event_name,
                message="",
                from_email=trainer.email,
                email_notification=trainer.get_preferences().email_send_reservation_emails,
                attachments=[ics],
                email_category=EmailCategory.TRAINING,
            )
    if user and should_send_ics(user, cancelled):
        # Now send to actual user with trainer as organizer
        ics = create_ics(
            training.id, event_name, training.start, training.end, user, organizer=trainer, cancelled=cancelled
        )
        user.email_user(
            subject=event_name,
            message="",
            from_email=trainer.email,
            email_notification=user.get_preferences().email_send_reservation_emails,
            attachments=[ics],
            email_category=EmailCategory.TRAINING,
        )


def should_send_ics(user: User, cancelled: bool = False):
    return (
        cancelled
        and user.get_preferences().attach_cancelled_reservation
        or not cancelled
        and user.get_preferences().attach_created_reservation
    )
