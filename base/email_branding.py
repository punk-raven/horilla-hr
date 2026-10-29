"""
The images every HTML email shows, embedded in the message itself.

Templates show an image with ``<img src="cid:<name>">``, where ``<name>`` is
registered in ``INLINE_IMAGES`` (the brand logo, the leave, ticket and payslip
illustrations). Any message whose HTML refers to a registered Content-ID gets
that PNG attached inline when it is built, once, so no sender has to attach
it; an image the HTML does not show is not attached. Images are never linked:
the production host sits behind Cloudflare Access, which answers a mail
client's image request with its login page, third-party hosts come and go,
and Gmail and Outlook block data: URIs.

Two hooks cover every way mail leaves the app:

* ``EmailMessage._create_attachments`` (patched by ``install()``) attaches the
  images and puts the inline parts in a ``multipart/related`` part with the
  HTML they belong to, files staying in ``multipart/mixed`` around it.
  Everything that serialises the MIME message goes through it: SMTP, the
  console, file and locmem backends, Outlook.
* ``ConfiguredEmailBackend`` calls ``attach_inline_images()`` before handing
  mail to the Resend API, which never builds MIME: Anymail sends each inline
  image as an attachment with a ``content_id``.
"""

from __future__ import annotations

import mimetypes
import os
import re
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from functools import lru_cache

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.mail import EmailMessage
from django.core.mail.message import SafeMIMEMultipart

BRAND_LOGO_CID = "brand_logo"
BRAND_LOGO_STATIC = "images/ui/punkraven-mail-logo.png"
PAYSLIP_HERO_CID = "payslip_hero"
# Content-ID -> static image. A message whose HTML shows cid:<name> gets the
# image attached inline.
INLINE_IMAGES = {
    BRAND_LOGO_CID: BRAND_LOGO_STATIC,
    "leave_icon": "images/ui/leave_types.png",
    "ticket_icon": "images/ui/ticket.png",
    PAYSLIP_HERO_CID: "images/ui/payslip-mail-hero.png",
}
CID_REFERENCE = re.compile(r"""cid:([^\s"'<>)]+)""")
# The sign-in button's fill (Ocean Blue's primary #3b82f6 mixed 85% with
# black): white text on it is 4.9:1, AA for normal text. The theme's own
# #3b82f6 is 3.7:1.
BRAND_BLUE = "#326ed1"
# Image types every major mail client shows inline.
MAIL_IMAGE_SUBTYPES = {"png", "jpeg", "gif"}


@lru_cache(maxsize=None)
def static_bytes(static_path: str) -> bytes:
    path = finders.find(static_path)
    if path:
        with open(path, "rb") as image:
            return image.read()
    with staticfiles_storage.open(static_path) as image:
        return image.read()


def brand_logo_bytes() -> bytes:
    return static_bytes(BRAND_LOGO_STATIC)


def inline_image(content: bytes, filename: str, cid: str = BRAND_LOGO_CID):
    """An image part the HTML shows through cid:<cid>."""
    mimetype = mimetypes.guess_type(filename)[0] or ""
    subtype = mimetype.split("/", 1)[1] if mimetype.startswith("image/") else None
    # Raises TypeError when neither the name nor the bytes say what image it is.
    part = MIMEImage(content, _subtype=subtype)
    part.add_header("Content-ID", f"<{cid}>")
    part.add_header("Content-Disposition", "inline", filename=filename)
    return part


def is_inline_part(attachment) -> bool:
    return (
        isinstance(attachment, MIMEBase)
        and "Content-ID" in attachment
        and attachment.get_content_disposition() != "attachment"
    )


def _html_parts(message: EmailMessage):
    if message.content_subtype == "html":
        yield str(message.body)
    for alternative in getattr(message, "alternatives", None) or ():
        content, mimetype = alternative[0], alternative[1]
        if mimetype == "text/html":
            yield str(content)


def referenced_cids(message: EmailMessage) -> list[str]:
    """The Content-IDs the message's HTML shows, in order, each once."""
    cids = []
    for html in _html_parts(message):
        for cid in CID_REFERENCE.findall(html):
            if cid not in cids:
                cids.append(cid)
    return cids


def attach_inline_images(message: EmailMessage) -> list[str]:
    """
    Attach inline every registered image the HTML shows that no part carries
    yet (a white-labelled company logo may already stand in for the brand
    logo). Safe to call more than once. Returns the Content-IDs it attached.
    """
    present = {
        part["Content-ID"].strip().strip("<>")
        for part in message.attachments
        if is_inline_part(part)
    }
    attached = []
    for cid in referenced_cids(message):
        static_path = INLINE_IMAGES.get(cid)
        if static_path is None or cid in present:
            continue
        message.attach(
            inline_image(static_bytes(static_path), os.path.basename(static_path), cid)
        )
        attached.append(cid)
    return attached


def attach_company_logo(message: EmailMessage, company) -> bool:
    """
    With WHITE_LABELLING on, show the company's own logo where the brand logo
    goes. Returns whether a company logo was attached.
    """
    if not getattr(settings, "WHITE_LABELLING", False) or not company:
        return False
    icon = getattr(company, "icon", None)
    if not icon:
        return False
    from horilla.media import read_media

    content = read_media(icon)
    if not content:
        return False
    try:
        part = inline_image(content, os.path.basename(icon.name))
    except TypeError:
        part = None
    if part is None or part.get_content_subtype() not in MAIL_IMAGE_SUBTYPES:
        # Not an image mail clients show (Gmail and Outlook drop SVG): the
        # brand logo stays.
        return False
    message.attach(part)
    return True


def _related(root, inline_parts, encoding):
    related = SafeMIMEMultipart(_subtype="related", encoding=encoding)
    # RFC 2387: the type parameter names the root part's type.
    related.set_param("type", root.get_content_type())
    related.attach(root)
    for part in inline_parts:
        related.attach(part)
    return related


def _create_attachments(self, msg):
    attach_inline_images(self)
    inline_parts = [a for a in self.attachments if is_inline_part(a)]
    if not inline_parts:
        return _django_create_attachments(self, msg)

    encoding = self.encoding or settings.DEFAULT_CHARSET
    if msg.get_content_type() == "multipart/alternative":
        # alternative[text, related[html, images]]: the images belong to the
        # HTML, and a text-only reader never sees them.
        payload = msg.get_payload()
        for index, part in enumerate(payload):
            if part.get_content_type() == "text/html":
                payload[index] = _related(part, inline_parts, encoding)
                break
        else:
            msg = _related(msg, inline_parts, encoding)
    else:
        msg = _related(msg, inline_parts, encoding)

    files = [a for a in self.attachments if not is_inline_part(a)]
    if not files:
        return msg
    mixed = SafeMIMEMultipart(_subtype=self.mixed_subtype, encoding=encoding)
    mixed.attach(msg)
    for attachment in files:
        if isinstance(attachment, MIMEBase):
            mixed.attach(attachment)
        else:
            mixed.attach(self._create_attachment(*attachment))
    return mixed


_django_create_attachments = EmailMessage._create_attachments


def install():
    """Build every EmailMessage with its inline images in a related part."""
    if EmailMessage._create_attachments is not _create_attachments:
        EmailMessage._create_attachments = _create_attachments
