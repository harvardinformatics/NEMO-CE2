import datetime
from logging import getLogger
from re import search

from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from NEMO.decorators import staff_member_or_tool_superuser_required
from NEMO.exceptions import ProjectChargeException
from NEMO.models import (
    Project,
    QualificationLevel,
    Tool,
    ToolQualificationGroup,
    TrainingEvent,
    TrainingSession,
    TrainingTechnique,
    User,
)
from NEMO.policy import policy_class as policy
from NEMO.utilities import datetime_input_format, quiet_int
from NEMO.views.customization import TrainingCustomization
from NEMO.views.qualifications import qualify

training_logger = getLogger(__name__)


@staff_member_or_tool_superuser_required
@require_GET
def training(request):
    """Present a web page to allow staff or tool superusers to charge training and qualify users on particular tools."""
    if TrainingCustomization.get_bool("training_module_enabled"):
        return redirect("record_training_events")
    return render(request, "training/training.html", get_training_dictionary(request))


def get_training_dictionary(request):
    user: User = request.user
    users = User.objects.filter(is_active=True).exclude(id=user.id)
    tools = Tool.objects.filter(
        Q(visible=True)
        | Q(visible=False) & Q(id__in=TrainingCustomization.get_list_int("training_included_hidden_tools"))
    )
    tool_groups = ToolQualificationGroup.objects.all()
    if not user.is_staff and user.is_tool_superuser:
        tools = tools.filter(_superusers__in=[user])
        # Superusers can only use groups if they are superusers for all those
        tool_groups = (
            tool_groups.annotate(num_tools=Count("tools")).filter(tools__in=tools).filter(num_tools=len(tools))
        )
    training_types = TrainingSession.Type.Choices
    training_only_type = TrainingCustomization.get_int("training_only_type")
    if training_only_type is not None:
        # only keep the one type
        training_types = [training_type for training_type in training_types if training_type[0] == training_only_type]
    return {
        "users": users,
        "tools": list(tools),
        "tool_groups": list(tool_groups),
        "charge_types": training_types,
        "qualification_levels": QualificationLevel.objects.all(),
        "techniques": TrainingTechnique.objects.all(),
    }


@staff_member_or_tool_superuser_required
@require_GET
def training_entry(request):
    entry_number = int(request.GET["entry_number"])
    dictionary = get_training_dictionary(request)
    dictionary["entry_number"] = entry_number
    # Pass in any eligible data to the training entry for prefilling
    eligible_data = ["duration", "technique_id", "charge_type_id"]
    for key, value in request.GET.items():
        if key in eligible_data:
            dictionary[key] = value
    return render(request, "training/training_entry.html", dictionary)


def is_valid_field(field):
    return (
        search(
            "^(chosen_user|chosen_tool|chosen_project|date|duration|charge_type|technique|qualify|comment)__[0-9]+$",
            field,
        )
        is not None
    )


@staff_member_or_tool_superuser_required
@require_POST
def charge_training(request):
    trainer: User = request.user
    date_allowed = TrainingCustomization.get_bool("training_allow_date")
    try:
        charges = {}
        for key, value in request.POST.items():
            if is_valid_field(key):
                attribute, separator, index = key.partition("__")
                index = int(index)
                if index not in charges:
                    charges[index] = TrainingSession()
                    charges[index].trainer = trainer
                if attribute == "chosen_user":
                    charges[index].trainee = User.objects.get(id=to_int_or_negative(value))
                if attribute == "chosen_tool":
                    chosen_type = request.POST.get(f"chosen_type{separator}{index}", "tool")
                    identifier = to_int_or_negative(value)
                    setattr(
                        charges[index],
                        "qualify_tools",
                        (
                            [Tool.objects.get(id=identifier)]
                            if chosen_type == "tool"
                            else ToolQualificationGroup.objects.get(id=identifier).tools.all()
                        ),
                    )
                    # Even with a group of tools, we only charge training on the first one
                    charges[index].tool = next(iter(charges[index].qualify_tools))
                    if not trainer.is_staff and trainer.is_tool_superuser:
                        if not set(charges[index].qualify_tools).issubset(trainer.superuser_for_tools.all()):
                            return HttpResponseBadRequest("The trainer is not authorized to train on this tool")
                if attribute == "chosen_project":
                    charges[index].project = Project.objects.get(id=to_int_or_negative(value))
                if attribute == "duration":
                    charges[index].duration = int(value)
                if value and attribute == "date" and date_allowed:
                    charges[index].date = datetime.datetime.strptime(value, datetime_input_format).astimezone()
                if attribute == "charge_type":
                    charges[index].type = int(value)
                if attribute == "comment":
                    charges[index].comment = value
                if attribute == "technique":
                    charges[index].technique_id = quiet_int(value, None)
                if attribute == "qualify":
                    qualification_level_id = quiet_int(value, None)
                    setattr(charges[index], "qualification_level_id", qualification_level_id)
                    charges[index].qualified = bool(value == "on" or qualification_level_id)

        for c in charges.values():
            c.full_clean()
            policy.check_billing_to_project(c.project, c.trainee, c.tool, c)
    except ProjectChargeException as e:
        return HttpResponseBadRequest(e.msg)
    except User.DoesNotExist:
        return HttpResponseBadRequest("Please select a trainee from the list")
    except Tool.DoesNotExist:
        return HttpResponseBadRequest("Please select a tool/group from the list")
    except ToolQualificationGroup.DoesNotExist:
        return HttpResponseBadRequest("Please select a tool/group from the list")
    except Project.DoesNotExist:
        return HttpResponseBadRequest("Please select a project from the list")
    except Exception as e:
        training_logger.exception(e)
        return HttpResponseBadRequest(
            "An error occurred while processing the training charges. None of the charges were committed to the database. Please review the form for errors and omissions then submit the form again."
        )
    else:
        for c in charges.values():
            if c.qualified:
                for tool in c.qualify_tools:
                    qualify(c.trainer, tool, c.trainee, c.qualification_level_id, c)
            c.save()
        if TrainingCustomization.get_bool("training_module_enabled"):
            # Force to None if empty
            training_event_id = request.POST.get("training_event_id", None) or None
            training_event = TrainingEvent.objects.filter(id=training_event_id).first()
            if training_event:
                training_event.finish(request.user)
        dictionary = {
            "title": "Success!",
            "content": "Training charges were successfully saved.",
            "redirect": reverse("training"),
        }
        return render(request, "display_success_and_redirect.html", dictionary)


def to_int_or_negative(value: str):
    try:
        return int(value)
    except ValueError:
        return -1
