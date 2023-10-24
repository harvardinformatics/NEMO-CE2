from typing import Dict, List

from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from NEMO.decorators import staff_member_required
from NEMO.models import (
    AreaAccessRecord,
    ConsumableWithdraw,
    Reservation,
    StaffCharge,
    TrainingSession,
    UsageEvent,
    User,
)
from NEMO.views.adjustment_requests import adjustment_eligible_items
from NEMO.views.customization import RemoteWorkCustomization, UserRequestsCustomization


@staff_member_required
@require_POST
def validate_staff_charge(request, staff_charge_id):
    staff_charge = get_object_or_404(StaffCharge, id=staff_charge_id)
    staff_charge.validated = True
    staff_charge.validated_by = request.user
    staff_charge.save()
    # Validate associated area access records
    for area_access_record in staff_charge.areaaccessrecord_set.all():
        validate_area_access_record(request, area_access_record.id)
    return HttpResponse()


@login_required
@require_POST
def validate_usage_event(request, usage_event_id):
    usage_event = get_object_or_404(UsageEvent, id=usage_event_id)
    usage_event.validated = True
    usage_event.validated_by = request.user
    usage_event.save()
    return HttpResponse()


@login_required
@require_POST
def validate_area_access_record(request, area_access_record_id):
    area_access = get_object_or_404(AreaAccessRecord, id=area_access_record_id)
    area_access.validated = True
    area_access.validated_by = request.user
    area_access.save()
    return HttpResponse()


@login_required
@require_POST
def validate_missed_reservation(request, reservation_id):
    missed_reservation = get_object_or_404(Reservation, id=reservation_id, missed=True)
    missed_reservation.validated = True
    missed_reservation.validated_by = request.user
    missed_reservation.save()
    return HttpResponse()


@login_required
@require_POST
def validate_training_session(request, training_session_id):
    training_session = get_object_or_404(TrainingSession, id=training_session_id)
    training_session.validated = True
    training_session.validated_by = request.user
    training_session.save()
    return HttpResponse()


@login_required
@require_POST
def validate_consumable_withdrawal(request, consumable_withdraw_id):
    withdraw = get_object_or_404(ConsumableWithdraw, id=consumable_withdraw_id)
    withdraw.validated = True
    withdraw.validated_by = request.user
    withdraw.save()
    return HttpResponse()


@login_required
@require_POST
def validate_charge(request, item_type_id=None, item_id=None):
    item_type = ContentType.objects.get_for_id(item_type_id)
    model_instance = item_type.model_class()()
    if isinstance(model_instance, UsageEvent):
        return validate_usage_event(request, item_id)
    elif isinstance(model_instance, AreaAccessRecord):
        return validate_area_access_record(request, item_id)
    elif isinstance(model_instance, TrainingSession):
        return validate_training_session(request, item_id)
    elif isinstance(model_instance, ConsumableWithdraw):
        return validate_consumable_withdrawal(request, item_id)
    elif isinstance(model_instance, StaffCharge):
        return validate_staff_charge(request, item_id)
    elif isinstance(model_instance, Reservation):
        return validate_missed_reservation(request, item_id)
    return HttpResponse()


def charges_to_validate(user: User) -> List:
    if UserRequestsCustomization.get_bool("charges_validation_enabled"):
        staff_charges_allowed = RemoteWorkCustomization.get_bool("remote_work_validation")
        date_limit = UserRequestsCustomization.get_date_limit()
        charge_filter: Dict = {"end__gte": date_limit} if date_limit else {}
        charge_filter["validated"] = False
        return adjustment_eligible_items(staff_charges_allowed, charge_filter, user)
    return []
