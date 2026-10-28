"""
Which address django-axes locks out, per AXES_IPWARE_META_PRECEDENCE_ORDER.

Behind a Cloudflare Tunnel REMOTE_ADDR is the cloudflared connector, the same
for every visitor, so keying lockouts on it lets one attacker lock everyone
out. AXES_CLIENT_IP_HEADER=HTTP_CF_CONNECTING_IP puts Cloudflare's single-IP
header first.
"""

from axes.helpers import get_client_ip_address
from django.test import RequestFactory, SimpleTestCase, override_settings

CONNECTOR = "10.250.0.7"
VISITOR = "203.0.113.42"


class AxesClientIpTests(SimpleTestCase):
    def request(self, **meta):
        return RequestFactory().post("/login/", REMOTE_ADDR=CONNECTOR, **meta)

    @override_settings(
        AXES_IPWARE_META_PRECEDENCE_ORDER=["HTTP_CF_CONNECTING_IP", "REMOTE_ADDR"]
    )
    def test_trusted_edge_header_identifies_the_visitor(self):
        request = self.request(HTTP_CF_CONNECTING_IP=VISITOR)

        self.assertEqual(get_client_ip_address(request), VISITOR)

    @override_settings(AXES_IPWARE_META_PRECEDENCE_ORDER=["REMOTE_ADDR"])
    def test_header_is_ignored_unless_configured(self):
        # Without the opt-in a client could send any value per attempt.
        request = self.request(HTTP_CF_CONNECTING_IP=VISITOR)

        self.assertEqual(get_client_ip_address(request), CONNECTOR)

    @override_settings(
        AXES_IPWARE_META_PRECEDENCE_ORDER=["HTTP_CF_CONNECTING_IP", "REMOTE_ADDR"]
    )
    def test_falls_back_to_remote_addr_without_the_header(self):
        # Railway's healthcheck reaches the container directly, with no edge.
        self.assertEqual(get_client_ip_address(self.request()), CONNECTOR)
