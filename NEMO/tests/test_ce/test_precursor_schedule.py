from datetime import datetime

from django.test import TestCase

from NEMO.models import ConfigurationPrecursorSchedule
from NEMO.utilities import beginning_of_the_day, end_of_the_day


class TestPrecursorSchedule(TestCase):
    def setUp(self):
        """Setup testing environment."""
        self.config = ConfigurationPrecursorSchedule()

    def test_get_date_range(self):
        self.config.saturday = False
        self.config.sunday = False

        # 16 is a Saturday, range should be Friday 15 to Sunday 17
        start_time = datetime.strptime("16-Dec-2023", "%d-%b-%Y")
        start_date, end_date = self.config.get_date_range(start_time)
        self.assertEqual(start_date, beginning_of_the_day(datetime.strptime("15-Dec-2023", "%d-%b-%Y")))
        self.assertEqual(end_date, end_of_the_day(datetime.strptime("17-Dec-2023", "%d-%b-%Y")))

        # 15 is a Friday, range should be Friday 15 to Sunday 17
        start_time = datetime.strptime("15-Dec-2023", "%d-%b-%Y")
        start_date, end_date = self.config.get_date_range(start_time)
        self.assertEqual(start_date, beginning_of_the_day(datetime.strptime("15-Dec-2023", "%d-%b-%Y")))
        self.assertEqual(end_date, end_of_the_day(datetime.strptime("17-Dec-2023", "%d-%b-%Y")))

        # 18 is a Monday, range should be Monday 18 to Monday 18
        start_time = datetime.strptime("18-Dec-2023", "%d-%b-%Y")
        start_date, end_date = self.config.get_date_range(start_time)
        self.assertEqual(start_date, beginning_of_the_day(datetime.strptime("18-Dec-2023", "%d-%b-%Y")))
        self.assertEqual(end_date, end_of_the_day(datetime.strptime("18-Dec-2023", "%d-%b-%Y")))

        # Remove Monday, 18 is a Monday, range should be Friday 15 to Monday 18
        self.config.monday = False
        start_time = datetime.strptime("18-Dec-2023", "%d-%b-%Y")
        start_date, end_date = self.config.get_date_range(start_time)
        self.assertEqual(start_date, beginning_of_the_day(datetime.strptime("15-Dec-2023", "%d-%b-%Y")))
        self.assertEqual(end_date, end_of_the_day(datetime.strptime("18-Dec-2023", "%d-%b-%Y")))

        # Remove Tuesday, 19 is a Monday, range should be Monday 18 to Tuesday 19
        self.config.monday = True
        self.config.tuesday = False
        start_time = datetime.strptime("19-Dec-2023", "%d-%b-%Y")
        start_date, end_date = self.config.get_date_range(start_time)
        self.assertEqual(start_date, beginning_of_the_day(datetime.strptime("18-Dec-2023", "%d-%b-%Y")))
        self.assertEqual(end_date, end_of_the_day(datetime.strptime("19-Dec-2023", "%d-%b-%Y")))
