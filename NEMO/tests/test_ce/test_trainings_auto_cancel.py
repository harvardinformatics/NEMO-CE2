import datetime

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from NEMO.models import (
    Tool,
    User,
    TrainingEvent,
)


class TrainingsAutoCancelTest(TestCase):
    def test_training_auto_cancel(self):
        user = User.objects.create(
            username="test_user", first_name="Testy", last_name="McTester", email="testy.mctester@example.com"
        )
        trainee = User.objects.create(
            username="test_trainee", first_name="Trainee", last_name="McTester", email="trainee.mctester@example.com"
        )
        tool = Tool.objects.create(name="Test tool")
        current_time = timezone.datetime.now().astimezone(timezone.get_current_timezone())
        start = current_time + timezone.timedelta(hours=2)
        end = start + timezone.timedelta(minutes=30)

        # This training has no auto_cancel
        training_1 = TrainingEvent.objects.create(
            creator=user,
            trainer=user,
            start=start,
            end=start + timezone.timedelta(minutes=30),
            tool=tool,
            capacity=3,
        )
        training_1.users.add(trainee)
        training_1.save()
        # This training has auto_cancel in the future
        training_2 = TrainingEvent.objects.create(
            creator=user,
            trainer=user,
            start=start,
            end=end,
            tool=tool,
            capacity=3,
            auto_cancel=current_time + timezone.timedelta(hours=1),
        )
        training_2.users.add(trainee)
        training_2.save()
        # This training has auto_cancel now
        training_3 = TrainingEvent.objects.create(
            creator=user, trainer=user, start=start, end=end, tool=tool, capacity=3, auto_cancel=current_time
        )
        training_3.users.add(trainee)
        training_3.save()
        # Calling auto-cancel, no training should auto cancel since one user is registered
        call_command("auto_cancel_training_sessions")
        self.assertFalse(TrainingEvent.objects.filter(id=training_1.id)[0].cancelled)
        self.assertFalse(TrainingEvent.objects.filter(id=training_2.id)[0].cancelled)
        self.assertFalse(TrainingEvent.objects.filter(id=training_3.id)[0].cancelled)
        # Removing trainees and retry
        training_1.users.clear()
        training_1.save()
        training_2.users.clear()
        training_2.save()
        training_3.users.clear()
        training_3.save()
        call_command("auto_cancel_training_sessions")
        # Second training should now be cancelled
        self.assertFalse(TrainingEvent.objects.filter(id=training_1.id)[0].cancelled)
        self.assertFalse(TrainingEvent.objects.filter(id=training_2.id)[0].cancelled)
        self.assertTrue(TrainingEvent.objects.filter(id=training_3.id)[0].cancelled)
        self.assertEqual(TrainingEvent.objects.filter(id=training_3.id)[0].cancellation_reason, "No user registered")
