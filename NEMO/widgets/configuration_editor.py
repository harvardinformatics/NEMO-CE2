from typing import Union

from django.forms import Widget
from django.template import Context, Template
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe


class ConfigurationEditor(Widget):
    template_name = "configuration/configuration_line_item.html"

    def render(self, name, value, attrs=None, **kwargs):
        result = ""
        for config in value["configurations"]:
            render_as_form = value.get("render_as_form", None)
            if render_as_form is None:
                render_as_form = not config.tool.in_use() and config.user_is_maintainer(value["user"])
            if len(config.range_of_configurable_items()) == 1:
                result += self._render_for_one(config, render_as_form)
            else:
                result += self._render_for_multiple(config, render_as_form)
        return mark_safe(result)

    def _render_for_one(self, config, render_as_form=None):
        from NEMO.models import Configuration

        config_item = config if isinstance(config, Configuration) else config.configurationprecursorslot_set.first()
        result = "<p><label class='form-inline'>" + escape(config.name)
        result += self._render_configuration_line_item("", config_item, 0, render_as_form).strip()
        result += "</label></p>"
        return result

    def _render_for_multiple(self, config, render_as_form=None):
        from NEMO.models import ConfigurationPrecursor, Configuration

        config: Union[ConfigurationPrecursor, Configuration] = config
        is_precursor = isinstance(config, ConfigurationPrecursor)
        result = "<p>" + escape(config.name) + ":<ul>"
        name = config.configurable_item_name or config.name
        if is_precursor:
            for config_slot in config.configurationprecursorslot_set.all():
                result += (
                    "<li>" + self._render_configuration_line_item(name, config_slot, 0, render_as_form, True) + "</li>"
                )
        else:
            for setting_index in config.range_of_configurable_items():
                item_name = name + f" #{setting_index + 1}"
                result += (
                    "<li>"
                    + self._render_configuration_line_item(item_name, config, setting_index, render_as_form, True)
                    + "</li>"
                )
        result += "</ul></p>"
        return result

    def _render_configuration_line_item(
        self, item_name, config_item, index, render_as_form=None, multiple=False
    ) -> str:
        from NEMO.models import ConfigurationPrecursorSlot, Configuration

        config_item: Union[Configuration, ConfigurationPrecursorSlot] = config_item
        is_precursor_slot = isinstance(config_item, ConfigurationPrecursorSlot)
        config = config_item.precursor_configuration if is_precursor_slot else config_item
        current_setting = None
        try:
            current_setting = config_item.setting if is_precursor_slot else config_item.get_current_setting(index)
        except IndexError:
            pass
        dictionary = {
            "config_item": config_item,
            "config": config,
            "index": index,
            "item_name": item_name,
            "multiple": multiple,
            "readonly": not render_as_form,
            "display_setting": self.display_setting(current_setting),
            "current_setting": current_setting,
            "url": reverse("tool_configuration", args=(["slot"] if is_precursor_slot else [])),
        }
        return render_to_string(self.template_name, dictionary)

    def display_setting(self, current_setting):
        from NEMO.views.customization import ToolCustomization

        template = ToolCustomization.get("tool_control_configuration_setting_template")
        contents = "{{ current_setting }}"
        try:
            contents = Template(template).render(Context({"current_setting": escape(current_setting)}))
        except:
            pass
        return contents
