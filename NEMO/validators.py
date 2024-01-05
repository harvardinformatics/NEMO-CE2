import re

from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _

COLOR_HEX_RE = "#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})"


color_hex_validator = RegexValidator(
    re.compile(r"^" + COLOR_HEX_RE + "$"),
    _("Enter a valid hex color, eg. #000000"),
    "invalid",
)


color_hex_list_validator = RegexValidator(
    re.compile(r"^" + COLOR_HEX_RE + "(?:,\s*" + COLOR_HEX_RE + ")*$"),
    message=_("Enter a valid hex color list, eg. #000000,#111111"),
    code="invalid",
)


def int_blank_list_validator(sep=",", message=None, code="invalid", allow_negative=False):
    regexp = re.compile(
        r"^%(neg)s\d*(?:%(sep)s%(neg)s\d*)*\Z"
        % {
            "neg": "(-)?" if allow_negative else "",
            "sep": re.escape(sep),
        }
    )
    return RegexValidator(regexp, message=message, code=code)


validate_comma_separated_integer_or_blank_list = int_blank_list_validator(
    message=_("Enter only digits separated by commas."),
)
