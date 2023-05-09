from django import template

from NEMO import utilities

register = template.Library()

@register.filter
def is_trainer(value, arg=None):
	return utilities.is_trainer(value, arg)


@register.filter
def training_event_invitations(value, arg=None):
	return value.pending_invitations(arg)
