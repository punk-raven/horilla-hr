import json

from django import forms
from django.utils.safestring import mark_safe


class CustomModelChoiceWidget(forms.Select):
    """
    A custom Django widget for rendering a select input with an optional delete button.

    This widget extends the standard Select widget to include a delete button.
    The URL for the delete action can be dynamically provided via the widget's
    attributes or constructor.

    Attributes:
        delete_url (str): The URL to be used for the delete button's action.
                        If not provided, the button will not be rendered.
    """

    def __init__(self, *args, **kwargs):
        # Remove default delete_url
        self.delete_url = None
        if "delete_url" in kwargs:
            self.delete_url = kwargs.pop("delete_url")
        super().__init__(*args, **kwargs)

    def render(self, name, value, attrs=None, renderer=None):
        attrs = attrs.copy() if attrs else {}
        # .oh-input/.oh-select carry a spacing margin-bottom (theme
        # v1_styles.css) that skews this flex row's vertical centering
        # against the delete button - the row spaces itself, so drop it.
        style = attrs.get("style", "")
        attrs["style"] = f"{style}; margin-bottom: 0" if style else "margin-bottom: 0"

        # Render the original widget
        original_html = super().render(name, value, attrs, renderer)

        # Get the delete_url from attributes if provided
        delete_url = attrs.get("delete_url", self.delete_url)

        # Create the custom HTML including the delete button
        custom_html = f"""
        <div class="pt-2" id="{name}">
            <div class="oh-input__group" style="display: flex; align-items: center; gap: 8px">
                <div style="flex: 1">{original_html}</div>
                {f'<button hx-get="{delete_url}" class="oh-btn oh-btn--danger oh-btn--sq-sm d-flex align-items-center justify-content-center flex-shrink-0" hx-target="#{name}" hx-swap="outerHTML" id="delete-link"><ion-icon name="trash-outline"></ion-icon></button>' if delete_url else ''}
            </div>
        </div>
        """
        return mark_safe(custom_html)

    from django import forms


class CustomTextInputWidget(forms.TextInput):
    """
    A custom Django widget for rendering a text input with an optional delete button.

    This widget extends the standard TextInput widget to include a delete button.
    The URL for the delete action can be dynamically provided via the widget's
    attributes or constructor.

    Attributes:
        delete_url (str): The URL to be used for the delete button's action.
                        If not provided, the button will not be rendered.
    """

    def __init__(self, *args, **kwargs):
        # Remove default delete_url
        self.delete_url = None
        if "delete_url" in kwargs:
            self.delete_url = kwargs.pop("delete_url")
        super().__init__(*args, **kwargs)

    def render(self, name, value, attrs=None, renderer=None):
        attrs = attrs.copy() if attrs else {}
        # .oh-input/.oh-select carry a spacing margin-bottom (theme
        # v1_styles.css) that skews this flex row's vertical centering
        # against the delete button - the row spaces itself, so drop it.
        style = attrs.get("style", "")
        attrs["style"] = f"{style}; margin-bottom: 0" if style else "margin-bottom: 0"

        # Render the original text input widget
        original_html = super().render(name, value, attrs, renderer)

        # Get the delete_url from attributes if provided
        delete_url = attrs.get("delete_url", self.delete_url)

        # Create the custom HTML including the delete button
        custom_html = f"""
        <div class="pt-2" id="{name}">
            <div class="oh-input__group" style="display: flex; align-items: center; gap: 8px">
                <div style="flex: 1">{original_html}</div>
                {f'<button hx-get="{delete_url}" class="oh-btn oh-btn--danger oh-btn--sq-sm d-flex align-items-center justify-content-center flex-shrink-0" hx-target="#{name}" hx-swap="outerHTML" id="delete-link"><ion-icon name="trash-outline"></ion-icon></button>' if delete_url else ''}
            </div>
        </div>
        """
        return mark_safe(custom_html)


class MailTransportSelect(forms.Select):
    """
    The mail server's "Send via" select. Hides the fields that apply to SMTP
    only while the Resend API is picked, and relabels the password as the
    API key. Rendered by the generic form view, so it carries its own script.
    """

    SMTP_ONLY_FIELDS = ["host", "port", "username", "use_tls", "use_ssl"]

    def __init__(self, *args, api_value, password_labels, **kwargs):
        self.api_value = api_value
        self.password_labels = password_labels
        super().__init__(*args, **kwargs)

    def render(self, name, value, attrs=None, renderer=None):
        html = super().render(name, value, attrs, renderer)
        select_id = (attrs or {}).get("id") or f"id_{name}"
        config = json.dumps(
            {
                "select": select_id,
                "api": self.api_value,
                "fields": self.SMTP_ONLY_FIELDS,
                "labels": {
                    key: str(label) for key, label in self.password_labels.items()
                },
            }
        )
        script = f"""
        <script>
            (function (config) {{
                const select = document.getElementById(config.select);
                const form = select && select.closest("form");
                if (!form) return;
                function apply() {{
                    const api = select.value === config.api;
                    config.fields.forEach(function (field) {{
                        const row = form.querySelector("#id_" + field + "_parent_div");
                        if (row) row.style.display = api ? "none" : "";
                    }});
                    const label = form.querySelector("#id_password_parent_div label");
                    if (label) label.textContent = api ? config.labels.api : config.labels.smtp;
                }}
                // select2 fires jQuery's change event, which native listeners miss.
                (window.jQuery ? jQuery(select) : null)?.on("change", apply);
                select.addEventListener("change", apply);
                apply();
                // When the form replaces one with the same field ids (the modal
                // reopened), htmx's settle step puts the rows' original style
                // back after this script ran, showing the SMTP fields again.
                document.addEventListener("htmx:afterSettle", function settle() {{
                    if (!select.isConnected) {{
                        document.removeEventListener("htmx:afterSettle", settle);
                        return;
                    }}
                    apply();
                }});
            }})({config});
        </script>
        """
        return mark_safe(html + script)
