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
        current_time = timezone.now()
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
        # This training has auto_cancel now
        training_3 = TrainingEvent.objects.create(
            creator=user, trainer=user, start=start, end=end, tool=tool, capacity=3, auto_cancel=current_time
        )
        training_3.users.add(trainee)
        # Calling auto-cancel, no training should auto cancel since one user is registered
        call_command("auto_cancel_training_sessions")
        self.assertFalse(TrainingEvent.objects.get(id=training_1.id).cancelled)
        self.assertFalse(TrainingEvent.objects.get(id=training_2.id).cancelled)
        self.assertFalse(TrainingEvent.objects.get(id=training_3.id).cancelled)
        # Removing trainees and retry
        training_1.users.clear()
        training_2.users.clear()
        training_3.users.clear()
        call_command("auto_cancel_training_sessions")
        # Second training should now be cancelled
        self.assertFalse(TrainingEvent.objects.get(id=training_1.id).cancelled)
        self.assertFalse(TrainingEvent.objects.get(id=training_2.id).cancelled)
        self.assertTrue(TrainingEvent.objects.get(id=training_3.id).cancelled)
        self.assertEqual(TrainingEvent.objects.get(id=training_3.id).cancellation_reason, "No user registered")
